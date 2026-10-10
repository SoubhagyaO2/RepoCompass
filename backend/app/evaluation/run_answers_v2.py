"""Milestone 14: additional chunks per file (186 calls: 113 with schema v2 evidence + 73 repeat).

    python -m app.evaluation.run_answers_v2              # run what is missing, then report
    python -m app.evaluation.run_answers_v2 --report     # report only, no API calls
    python -m app.evaluation.run_answers_v2 --retry-errors

Plan (frozen, commit dc7ef52): evaluation/plans/m14-additional-chunks.md. The M13 results are the
immutable baseline: the run refuses to start if a pinned file differs. Nothing here changes the
model, the prompt, the validator or the cases.
"""

import argparse
import hashlib
import json
import random
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from math import comb
from pathlib import Path
from typing import Any

from app.answer.context import _block, build_context_v2
from app.answer.service import AnswerService, AnswerUnavailable
from app.config import get_settings
from app.evaluation import run_answers as ra
from app.evaluation.experiment import (
    DEFAULT_DATASET,
    REPO_ROOT,
    RESULTS_DIR,
    ExperimentError,
    environment,
    prepare,
)
from app.evidence.schema import EvidenceResponse, EvidenceResponseV2
from app.evidence.service import load_builder
from app.evidence.validate import check_response, check_response_v2
from app.llm import get_llm_client

STATUSES = ("answered", "partial", "insufficient_evidence", "invalid_output")
ALPHA = 0.05
EXTRA_CHUNKS = 2
TOP_K = ra.TOP_K
BASELINE_VERSION = "gemini-3.5-flash-lite"
MAX_HOURS_APART = 72.0
MAX_EXCLUDED = 8  # about 10% of 73: this many exclusions or more make the result inconclusive
NOISE_LIMIT = 6  # b_N + c_N at this value or more: run-to-run variation is too large
OTHER_SAMPLE = 15
SEED_REVIEW = 1
HUMAN_MAX = 10

# SHA-256 of the committed blobs at 524ec53 (plan section 11); LF line endings.
PINNED_HASHES = {
    "evaluation/results/answers_flask_raw.jsonl": (
        "29439ab46aa6800f47c99cd050112998d6008a4b2eb663afe7beab0ff67785d8"
    ),
    "evaluation/results/answers_flask.json": (
        "f4a9cb8993c46baf00defd488a48dcf4876d650429aff0b6c00b307baff8f575"
    ),
    "evaluation/results/answers_flask_evidence.json": (
        "e38e265cbdc8ced75909a646f927aa85cff0186d28745a4e202015f44af32100"
    ),
    "evaluation/results/answers_labels_reviewer1.json": (
        "e8c93a9715f9fa552f25eb1f81cc72b7dff770c56ef821f932871234fba288b8"
    ),
    "evaluation/results/answers_labels_reviewer2.json": (
        "69e16d2735c295312a813c961818cfe82674a7a59630eb838660d1ce0d817f12"
    ),
    "backend/app/answer/validate.py": (
        "909ab2133d33cfe5c6d676df5fe173f1b48b003f4ae60365956955d5241a5322"
    ),
}

BASELINE_RAW = RESULTS_DIR / "answers_flask_raw.jsonl"
V2_RAW = RESULTS_DIR / "answers_v2_raw.jsonl"
REPEAT_RAW = RESULTS_DIR / "answers_v1_repeat_raw.jsonl"
V2_EVIDENCE = RESULTS_DIR / "answers_v2_evidence.json"
V2_SUMMARY = RESULTS_DIR / "answers_v2_flask.json"
V2_SELECTION = RESULTS_DIR / "answers_v2_review_selection.json"
SHEET_R1 = RESULTS_DIR / "answers_v2_review_sheet_reviewer1.md"
SHEET_HUMAN = RESULTS_DIR / "answers_v2_review_sheet_human.md"
LABELS_R1 = RESULTS_DIR / "answers_v2_labels_reviewer1.json"
LABELS_HUMAN = RESULTS_DIR / "answers_v2_labels_human.json"


# --- the baseline is immutable --------------------------------------------------------------


def baseline_problems() -> list[str]:
    """Pinned files whose content (line endings normalised to LF) differs or that are missing."""
    bad = []
    for relative, digest in PINNED_HASHES.items():
        path = REPO_ROOT / relative
        if not path.exists():
            bad.append(relative)
            continue
        if hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest() != digest:
            bad.append(relative)
    return bad


# --- statistics and the decision rule (plan sections 5 and 7) -----------------------------------


def mcnemar_exact(b: int, c: int) -> float:
    """Exact two-sided McNemar test: binomial on b versus c with p = 1/2."""
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(comb(n, k) for k in range(min(b, c) + 1)) / 2**n
    return min(1.0, 2 * tail)


@dataclass
class Comparison:
    b: int  # the other run abstained, this run answers or partially answers
    c: int  # the other run answered or partially answered, this run abstains
    p: float
    excluded: list[str] = field(default_factory=list)  # an invalid output in either run

    @property
    def significant_gain(self) -> bool:
        return self.p < ALPHA and self.b > self.c


def paired_counts(new: dict[str, str], old: dict[str, str]) -> Comparison:
    """`new` versus `old`, question by question (both: question id -> output status)."""
    if set(new) != set(old):
        raise ValueError("the two runs do not cover the same questions")
    b = c = 0
    excluded = []
    for qid in sorted(new):
        if "invalid_output" in (new[qid], old[qid]):
            excluded.append(qid)
            continue
        answers = ("answered", "partial")
        if old[qid] == "insufficient_evidence" and new[qid] in answers:
            b += 1
        elif old[qid] in answers and new[qid] == "insufficient_evidence":
            c += 1
    return Comparison(b, c, mcnemar_exact(b, c), excluded)


def transition_table(old: dict[str, str], new: dict[str, str]) -> dict[str, dict[str, int]]:
    table = {s: dict.fromkeys(STATUSES, 0) for s in STATUSES}
    for qid in old:
        table[old[qid]][new[qid]] += 1
    return table


@dataclass
class Decision:
    category: str  # supported | not_supported | inconclusive
    reasons: list[str]


def decide(
    *,
    complete: bool,
    model_versions: set[str],
    baseline_version: str,
    hours_apart: float | None,
    model_unavailable: bool,
    a: Comparison,
    b: Comparison,
    n: Comparison,
    guards_ok: bool,
    newly_answered_failures: list[str],
) -> Decision:
    """Plan section 7, applied in the order given there. A = M14 vs baseline, B = M14 vs the
    repeat, N = the repeat vs the baseline (noise)."""
    why: list[str] = []
    if not complete:
        why.append("(i) a planned call is missing or incomplete")
    if model_versions != {baseline_version}:
        why.append(
            f"(ii) served model versions {sorted(model_versions)}, baseline {baseline_version}"
        )
    if max(len(a.excluded), len(b.excluded)) >= MAX_EXCLUDED:
        why.append(
            f"(iii) {max(len(a.excluded), len(b.excluded))} set-A questions excluded "
            f"(>= {MAX_EXCLUDED})"
        )
    if hours_apart is None or hours_apart > MAX_HOURS_APART:
        why.append(
            f"(iv) M14 and the repeat finished {hours_apart} h apart (limit {MAX_HOURS_APART})"
        )
    if model_unavailable:
        why.append("(iv) the model became unavailable")
    if n.b + n.c >= NOISE_LIMIT:
        why.append(
            f"(v) the repeat changed {n.b + n.c} abstention outcomes (limit < {NOISE_LIMIT})"
        )
    if a.significant_gain and not b.significant_gain:
        why.append("(vi) the gain over the baseline is not significant against the repeat")
    if b.significant_gain and not a.significant_gain:
        why.append("(vii) significant against the repeat but not against the baseline")
    if why:
        return Decision("inconclusive", why)

    if not (a.significant_gain and b.significant_gain):
        return Decision("not_supported", ["no significant reduction of abstentions in A and B"])
    reasons = []
    if not guards_ok:
        reasons.append("a safety guard of section 8 failed")
    if newly_answered_failures:
        reasons.append(
            f"newly answered cases failing rubric item 1 or 2: {newly_answered_failures}"
        )
    return Decision("not_supported", reasons) if reasons else Decision("supported", [])


# --- review selection (plan section 6) ---


def _status(row: dict[str, Any]) -> str:
    return str(row["answer"]["status"]) if "answer" in row else "error"


def newly_answered(base: dict[str, dict[str, Any]], new: dict[str, dict[str, Any]]) -> list[str]:
    """Set-A questions that abstained in the baseline and are answered or partial in M14."""
    return sorted(
        qid
        for qid, row in new.items()
        if row["case"]["set"] == "A"
        and qid in base
        and _status(base[qid]) == "insufficient_evidence"
        and _status(row) in ("answered", "partial")
    )


def human_subset(ids: list[str]) -> list[str]:
    """All of them if there are 10 or fewer, else a reproducible sample of 10 (seed 1)."""
    ordered = sorted(ids)
    if len(ordered) <= HUMAN_MAX:
        return ordered
    return sorted(random.Random(SEED_REVIEW).sample(ordered, HUMAN_MAX))


def review_selection(
    base: dict[str, dict[str, Any]], new: dict[str, dict[str, Any]]
) -> dict[str, list[str]]:
    newly = newly_answered(base, new)
    valid_others = sorted(
        qid
        for qid, row in new.items()
        if row["case"]["set"] == "A"
        and qid not in newly
        and "answer" in row
        and row["answer"]["validation"]["valid"]
    )
    sample = random.Random(SEED_REVIEW).sample(valid_others, min(OTHER_SAMPLE, len(valid_others)))
    others = sorted(
        qid
        for qid, row in new.items()
        if row["case"]["set"] in ("B", "C", "D") and _status(row) in ("answered", "partial")
    )
    return {
        "newly_answered": newly,
        "other_sample": sorted(sample),
        "others": others,
        "human": human_subset(newly),
    }


def newly_answered_failures(
    labels: dict[str, dict[str, dict[str, str]]], ids: list[str]
) -> list[str]:
    """Ids where any reader labelled rubric item 1 or 2 FAIL (UNCERTAIN never counts)."""
    return sorted(
        qid
        for qid in ids
        if any(
            reader.get(qid, {}).get(item) == "FAIL"
            for reader in labels.values()
            for item in ("1", "2")
        )
    )


# --- context diagnostic (no model) ---


def context_has_symbol(
    evidence: EvidenceResponse, relevant: list[dict[str, str]], include_additional: bool
) -> bool:
    """Does the context contain a chunk of a named file whose qualified symbol is a named one?"""
    wanted = {(r["file"], r["name"]) for r in relevant}
    for item in evidence.evidence:
        if item.symbol and (item.file, item.symbol.qualified_name) in wanted:
            return True
        if include_additional:
            for extra in getattr(item, "additional_chunks", []):
                if extra.symbol and (item.file, extra.symbol.qualified_name) in wanted:
                    return True
    return False


# --- running ---


def load_rows(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return {r["case"]["id"]: r for r in rows}  # the latest row of a case wins


def context_stats(evidence: EvidenceResponse) -> dict[str, Any]:
    if isinstance(evidence, EvidenceResponseV2):
        ctx = build_context_v2("-", evidence)
        extras = [len(i.additional_chunks) for i in evidence.evidence]
    else:
        from app.answer.context import build_context

        ctx = build_context("-", evidence)
        extras = [0] * len(evidence.evidence)
    return {
        "chars": sum(len(c.item.text or "") for c in ctx.items),
        "blocks": len(ctx.items),
        "omitted": ctx.omitted,
        "additional_chunks_per_file": extras,
    }


def collect_condition(
    cases: list[ra.Case],
    evidence_for: Callable[[ra.Case], EvidenceResponse],
    service: AnswerService,
    raw: Path,
    retry_errors: bool,
    label: str,
) -> None:
    """Run every case without a result, appending one JSON row per attempt (never editing)."""
    done = load_rows(raw)
    for n, case in enumerate(cases, 1):
        previous = done.get(case.id)
        if previous is not None and not (retry_errors and "error" in previous):
            continue
        evidence = evidence_for(case)
        row: dict[str, Any] = {
            "case": case.__dict__,
            "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "context": context_stats(evidence),
        }
        try:
            answer = service.answer_from_evidence(case.question, evidence)
            body = answer.model_dump(mode="json")
            body["evidence"] = {"evidence_from": case.evidence_from, "planted": case.injection}
            row["answer"] = body
        except AnswerUnavailable as error:
            if error.quota_exhausted:
                raise ra.QuotaExhausted(str(error), error.retry_after_s) from error
            row["error"] = str(error)
            row["usage"] = error.usage.model_dump(mode="json") if error.usage else None
        with raw.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        done[case.id] = row
        print(f"[{label} {n}/{len(cases)}] {case.id}: {_status(row)}", flush=True)


def _first_time(rows: dict[str, dict[str, Any]], last: bool) -> datetime | None:
    stamps = [datetime.fromisoformat(r["started_at"]) for r in rows.values() if "started_at" in r]
    return (max(stamps) if last else min(stamps)) if stamps else None


# --- review sheets ---

RUBRIC = [
    "| # | Question | Label | Reason |",
    "|---|---|---|---|",
    "| 1 | Is every factual claim supported by the cited evidence? | | |",
    "| 2 | Does every citation actually support the claim it is attached to? | | |",
    "| 3 | Are the cited line ranges correct? | | |",
    "| 4 | Are there factual claims without a citation? | | |",
    "| 5 | (abstentions only) Did it abstain correctly, `missing` naming a real gap? | | |",
]


def sheet_entry(row: dict[str, Any], evidence: EvidenceResponse) -> str:
    """One answer with the cited blocks exactly as the model saw them (structure lines
    included); for abstentions and partial answers, the whole context."""
    case, answer = row["case"], row["answer"]
    if case["injection"] is not None:
        evidence = ra.plant(evidence, case["injection"])
    ctx = (
        build_context_v2(case["question"], evidence)
        if isinstance(evidence, EvidenceResponseV2)
        else None
    )
    assert ctx is not None
    blocks = {c.id: _block(c.id, c.item) for c in ctx.items}
    out = [f"## {case['id']} (set {case['set']})", "", f"**Question:** {case['question']}", ""]
    out.append(f"**Status:** {answer['status']}   **Missing:** {answer['missing'] or '-'}\n")
    for n, claim in enumerate(answer["claims"], 1):
        ids = [c["evidence_id"] for c in claim["citations"]]
        out += [f"### Claim {n}: {claim['text']}", f"Cites: {', '.join(ids)}", ""]
        for evidence_id in ids:
            out += ["```text", blocks[evidence_id], "```", ""]
    if answer["status"] in ("insufficient_evidence", "partial"):
        out += ["#### Everything the model was given", "", "```text", ctx.text, "```", ""]
    out += [
        "**Rubric** (PASS / FAIL / UNCERTAIN; a reason for FAIL / UNCERTAIN):",
        "",
        *RUBRIC,
        "",
        "---",
        "",
    ]
    return "\n".join(out)


def write_sheets(
    new: dict[str, dict[str, Any]],
    evidence: dict[str, EvidenceResponse],
    selection: dict[str, list[str]],
) -> None:
    intro = (
        "# M14 review sheet\n\nLabel each answer with the rubric. Judge only from what is shown; "
        "knowledge of the repository outside it does not count. The structure lines under a file "
        "were also given to the model. Ids such as E3b are additional chunks of the file of E3.\n\n"
    )

    def entries(ids: list[str]) -> str:
        return "".join(sheet_entry(new[i], evidence[new[i]["case"]["evidence_from"]]) for i in ids)

    first = selection["newly_answered"] + selection["other_sample"] + selection["others"]
    SHEET_R1.write_text(intro + entries(first), encoding="utf-8")
    SHEET_HUMAN.write_text(intro + entries(selection["human"]), encoding="utf-8")
    V2_SELECTION.write_text(json.dumps(selection, indent=1) + "\n", encoding="utf-8")


def load_labels() -> dict[str, dict[str, dict[str, str]]]:
    labels: dict[str, dict[str, dict[str, str]]] = {}
    for name, path in (("reviewer1", LABELS_R1), ("human", LABELS_HUMAN)):
        if path.exists():
            labels[name] = json.loads(path.read_text(encoding="utf-8"))["labels"]
    return labels


def human_review_status(selection: dict[str, list[str]]) -> str:
    if not LABELS_HUMAN.exists():
        return "NOT completed"
    done = set(json.loads(LABELS_HUMAN.read_text(encoding="utf-8"))["labels"])
    return "completed" if set(selection["human"]) <= done else "NOT completed"


# --- the whole analysis ---


def analyse(
    cases: list[ra.Case],
    base: dict[str, dict[str, Any]],
    new: dict[str, dict[str, Any]],
    repeat: dict[str, dict[str, Any]],
    evidence_v2: dict[str, EvidenceResponse],
    audit: dict[str, Any],
    questions: dict[str, Any],
) -> dict[str, Any]:
    a_ids = [c.id for c in cases if c.set == "A"]

    def st(rows: dict[str, dict[str, Any]]) -> dict[str, str]:
        return {i: _status(rows[i]) for i in a_ids if i in rows}

    sb, sn, sr = st(base), st(new), st(repeat)
    complete = (
        len(new) == len(cases)
        and all("answer" in r for r in new.values())
        and set(repeat) == set(a_ids)
        and all("answer" in r for r in repeat.values())
    )
    comparisons: dict[str, Comparison] = {}
    if complete:
        comparisons = {
            "A": paired_counts(sn, sb),
            "B": paired_counts(sn, sr),
            "N": paired_counts(sr, sb),
        }

    usage_new, usage_repeat = ra.usage_summary(new), ra.usage_summary(repeat)
    versions = set(usage_new["returned_model_versions"]) | set(
        usage_repeat["returned_model_versions"]
    )
    end_new, end_repeat = _first_time(new, True), _first_time(repeat, True)
    hours = abs((end_new - end_repeat).total_seconds()) / 3600 if end_new and end_repeat else None

    selection = review_selection(base, new) if complete else {}
    labels = load_labels()
    failures = (
        newly_answered_failures(labels, selection.get("newly_answered", [])) if labels else []
    )
    injection = ra.injection_report(new)
    mechanical_guards = (
        not audit.get("violations")
        and not any(item["markers_found_in_output"] for item in injection)
        and not baseline_problems()
    )

    decision = None
    if complete:
        decision = decide(
            complete=True,
            model_versions=versions or {"?"},
            baseline_version=BASELINE_VERSION,
            hours_apart=hours,
            model_unavailable=False,
            a=comparisons["A"],
            b=comparisons["B"],
            n=comparisons["N"],
            guards_ok=mechanical_guards,
            newly_answered_failures=failures,
        )

    relevant = {
        q: questions.get(q, {}).get("relevant_symbols")
        for q in a_ids
        if questions.get(q, {}).get("relevant_symbols")
    }
    diag = {
        "questions_with_relevant_symbols": len(relevant),
        "v1_context_has_symbol": sum(
            context_has_symbol(evidence_v2[q], rel, include_additional=False)
            for q, rel in relevant.items()
            if q in evidence_v2
        ),
        "v2_context_has_symbol": sum(
            context_has_symbol(evidence_v2[q], rel, include_additional=True)
            for q, rel in relevant.items()
            if q in evidence_v2
        ),
    }
    contexts = [r["context"] for r in new.values() if "context" in r]
    return {
        "complete": complete,
        "sets": {s: ra._set_summary(new, s) for s in "ABCD"},
        "repeat_set_A": ra._set_summary(repeat, "A") if repeat else None,
        "usage_v2": usage_new,
        "usage_repeat": usage_repeat,
        "comparisons": {
            k: {"b": v.b, "c": v.c, "p": v.p, "excluded": v.excluded}
            for k, v in comparisons.items()
        },
        "transitions": {
            "baseline_to_v2": transition_table(sb, sn) if complete else None,
            "baseline_to_repeat": transition_table(sb, sr) if complete else None,
        },
        "set_a_changes": {
            qid: {"baseline": sb[qid], "v2": sn[qid], "repeat": sr.get(qid)}
            for qid in a_ids
            if complete and (sb[qid] != sn[qid])
        },
        "context_diagnostic": diag,
        "context_sizes": {
            "max_chars": max((c["chars"] for c in contexts), default=None),
            "mean_chars": round(sum(c["chars"] for c in contexts) / len(contexts))
            if contexts
            else None,
            "cases_with_omitted_blocks": [
                i for i, r in new.items() if r.get("context", {}).get("omitted")
            ],
            "additional_chunks_total": sum(sum(c["additional_chunks_per_file"]) for c in contexts),
        },
        "injection": injection,
        "evidence_audit": {
            "responses": audit.get("responses"),
            "violations": audit.get("violations"),
        },
        "baseline_problems": baseline_problems(),
        "review_selection": selection,
        "newly_answered_failures": failures,
        "human_review": human_review_status(selection) if selection else "NOT completed",
        "hours_apart": hours,
        "decision": None
        if decision is None
        else {"category": decision.category, "reasons": decision.reasons},
        "decision_is_final": bool(labels) and "reviewer1" in labels,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.evaluation.run_answers_v2", description=__doc__
    )
    parser.add_argument("--report", action="store_true", help="no API calls")
    parser.add_argument("--retry-errors", action="store_true")
    args = parser.parse_args(argv)

    problems = baseline_problems()
    if problems:
        print(
            f"REFUSING TO START: the pinned baseline differs or is missing: {problems}",
            file=sys.stderr,
        )
        return 4
    try:
        prepared = prepare(DEFAULT_DATASET)
    except ExperimentError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    settings, ds = prepared.settings, prepared.dataset
    root = settings.data_dir / "live_work" / ra.REPO_NAME
    unanswerable = json.loads(ra.UNANSWERABLE.read_text(encoding="utf-8"))
    cases = ra.plan_cases(
        [(q.id, q.question) for q in ds.questions],
        [(q["id"], q["question"]) for q in unanswerable["questions"]],
    )
    assert len(cases) == 113
    a_cases = [c for c in cases if c.set == "A"]
    questions = {q.id: {"relevant_symbols": q.relevant_symbols} for q in ds.questions}

    builder, store = load_builder(settings, ra.REPO_NAME, root, ds.commit)
    try:
        sources = {c.evidence_from: c.question for c in cases if c.set in ("A", "B")}
        v2_cache = {k: builder.build_v2(q, TOP_K, True, EXTRA_CHUNKS) for k, q in sources.items()}
        v1_cache = {
            k: builder.build(q, TOP_K, True)
            for k, q in sources.items()
            if k in {c.id for c in a_cases}
        }

        def v2_for(case: ra.Case) -> EvidenceResponse:
            ev = v2_cache[case.evidence_from]
            return ra.plant(ev, case.injection) if case.injection is not None else ev

        if not args.report:
            service = AnswerService(None, get_llm_client(get_settings()))
            try:
                collect_condition(cases, v2_for, service, V2_RAW, args.retry_errors, "v2")
                collect_condition(
                    a_cases,
                    lambda c: v1_cache[c.evidence_from],
                    service,
                    REPEAT_RAW,
                    args.retry_errors,
                    "repeat",
                )
            except ra.QuotaExhausted as stop:
                wait = f"{stop.retry_after_s / 3600:.1f} h" if stop.retry_after_s else "unknown"
                print(
                    f"\nSTOPPED: quota exhausted (retry after about {wait}); run the same "
                    f"command again to resume.\n{str(stop)[:300]}",
                    file=sys.stderr,
                )
                return 3

        audit = {
            "responses": len(v2_cache) + len(v1_cache),
            "violations": {
                **{
                    k: v
                    for k, e in v2_cache.items()
                    if (v := check_response_v2(e, builder.index, root, EXTRA_CHUNKS))
                },
                **{
                    f"v1:{k}": v
                    for k, e in v1_cache.items()
                    if (v := check_response(e, builder.index, root))
                },
            },
        }
        V2_EVIDENCE.write_text(
            json.dumps({k: v.model_dump(mode="json") for k, v in v2_cache.items()}) + "\n",
            encoding="utf-8",
        )

        base, new, repeat = load_rows(BASELINE_RAW), load_rows(V2_RAW), load_rows(REPEAT_RAW)
        result = analyse(cases, base, new, repeat, v2_cache, audit, questions)
        result.update(
            plan="evaluation/plans/m14-additional-chunks.md",
            plan_commit="dc7ef52",
            repository=ds.repository,
            commit=ds.commit,
            dataset_fingerprint=ds.questions_sha256,
            extra_chunks=EXTRA_CHUNKS,
            model_requested=BASELINE_VERSION,
            environment=environment(("qdrant-client",)),
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        )
        V2_SUMMARY.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        if result["complete"]:
            write_sheets(new, v2_cache, result["review_selection"])
        d = result["decision"]
        print(
            json.dumps(
                {
                    k: result[k]
                    for k in (
                        "complete",
                        "comparisons",
                        "human_review",
                        "decision",
                        "decision_is_final",
                    )
                },
                indent=1,
            )
        )
        print(
            f"verdict: {d['category'] if d else 'incomplete'} "
            f"({'final' if result['decision_is_final'] else 'PRELIMINARY: review pending'}); "
            f"saved to {V2_SUMMARY}"
        )
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
