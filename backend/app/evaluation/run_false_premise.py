"""Milestone 15, development phase (plan section 4): choose among at most three prompt-v3
candidates on the 8 development pairs, plus the 3 known failures (reported, not scored).

    python -m app.evaluation.run_false_premise --arm baseline|c1|c2|c3   # run what is missing
    python -m app.evaluation.run_false_premise --report                  # no API calls

Plan (frozen step 1, commit 1475c09): evaluation/plans/m15-false-premise.md. The test pairs are
never read here. Exit codes: 3 quota exhausted (resume later), 4 credential or baseline problem.
"""

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.answer import prompt_v3
from app.answer.service import AnswerService
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
from app.evaluation.run_answers_v2 import (
    EXTRA_CHUNKS,
    TOP_K,
    baseline_problems,
    collect_condition,
    load_rows,
)
from app.evidence.service import load_builder
from app.llm import get_llm_client
from app.llm.base import LLMConfigError, LLMQuotaError

DEV_PAIRS = REPO_ROOT / "evaluation" / "datasets" / "m15_pairs_dev.json"
TEST_PAIRS = REPO_ROOT / "evaluation" / "datasets" / "m15_pairs_test.json"
CHOSEN = "c1"  # frozen at step 2 (plan section 13)
TEST_ARMS = ("baseline", CHOSEN)
ARMS = ("baseline", *prompt_v3.CANDIDATES)
SUMMARY = RESULTS_DIR / "m15_dev.json"


def raw_path(arm: str, phase: str = "dev") -> Path:
    return RESULTS_DIR / f"m15_{phase}_{arm}_raw.jsonl"


# --- scoring (plan section 5) ---


def pair_correct(p_status: str, q_status: str) -> bool:
    """The false-premise question is abstained on and its true twin is answered; format failures
    count as incorrect, so abstaining everywhere or answering everywhere scores zero."""
    return p_status == "insufficient_evidence" and q_status in ("answered", "partial")


def correct_pairs(pairs: list[tuple[str, str]], status: dict[str, str]) -> int:
    return sum(pair_correct(status[p], status[q]) for p, q in pairs)


@dataclass(frozen=True)
class ArmScore:
    correct: int
    invalid: int
    prompt_chars: int


def select_candidate(scores: dict[str, ArmScore]) -> str:
    """Most correct pairs; ties: fewer invalid outputs, then the shorter prompt, then name."""
    return min(
        scores, key=lambda n: (-scores[n].correct, scores[n].invalid, scores[n].prompt_chars, n)
    )


def _status(row: dict[str, Any]) -> str:
    return row["answer"]["status"] if "answer" in row else "error"


def score_arm(
    rows: dict[str, dict[str, Any]], pairs: list[tuple[str, str]], prompt_chars: int
) -> ArmScore:
    status = {cid: _status(r) for cid, r in rows.items()}
    ids = {i for pair in pairs for i in pair}
    return ArmScore(
        correct=correct_pairs(pairs, status),
        invalid=sum(status.get(i) == "invalid_output" for i in ids),
        prompt_chars=prompt_chars,
    )


def known_failures(rows: dict[str, dict[str, Any]]) -> dict[str, str]:
    return {cid: _status(r) for cid, r in rows.items() if cid.startswith("unans-")}


# --- the development questions ---


def dev_cases() -> tuple[list[ra.Case], list[tuple[str, str]]]:
    return pair_cases(DEV_PAIRS)


def test_cases() -> tuple[list[ra.Case], list[tuple[str, str]]]:
    return pair_cases(TEST_PAIRS)


def pair_cases(path: Path) -> tuple[list[ra.Case], list[tuple[str, str]]]:
    dev = json.loads(path.read_text(encoding="utf-8"))
    cases: list[ra.Case] = []
    pairs: list[tuple[str, str]] = []
    for pair in dev["pairs"]:
        p, q = pair["p"], pair["q"]
        cases.append(ra.Case(p["id"], "P", p["question"], p["id"]))
        cases.append(ra.Case(q["id"], "Q", q["question"], q["id"]))
        pairs.append((p["id"], q["id"]))
    unanswerable = {
        x["id"]: x["question"]
        for x in json.loads(ra.UNANSWERABLE.read_text(encoding="utf-8"))["questions"]
    }
    for known in dev.get("known_failures", []):
        cases.append(ra.Case(known, "KNOWN", unanswerable[known], known))
    return cases, pairs


# --- running ---


def make_service(arm: str, llm: Any) -> AnswerService:
    if arm == "baseline":
        return AnswerService(None, llm)
    return AnswerService(None, llm, system_prompt=prompt_v3.CANDIDATES[arm], contract="v3")


def prompt_chars(arm: str) -> int:
    from app.answer.prompt import SYSTEM_PROMPT

    return len(SYSTEM_PROMPT if arm == "baseline" else prompt_v3.CANDIDATES[arm])


def report(pairs: list[tuple[str, str]]) -> dict[str, Any]:
    arms: dict[str, Any] = {}
    scores: dict[str, ArmScore] = {}
    for arm in ARMS:
        rows = load_rows(raw_path(arm))
        if len(rows) < 19:
            arms[arm] = {"complete": False, "rows": len(rows)}
            continue
        score = score_arm(rows, pairs, prompt_chars(arm))
        status = {cid: _status(r) for cid, r in rows.items()}
        arms[arm] = {
            "complete": True,
            "correct_pairs": score.correct,
            "pairs": len(pairs),
            "invalid_outputs_in_pairs": score.invalid,
            "p_abstained": sum(status[p] == "insufficient_evidence" for p, _ in pairs),
            "q_answered": sum(status[q] in ("answered", "partial") for _, q in pairs),
            "known_failures": known_failures(rows),
            "prompt_chars": score.prompt_chars,
            "model_versions": sorted(
                {
                    r["answer"]["model"]["returned_model_version"] or "?"
                    for r in rows.values()
                    if "answer" in r
                }
            ),
            "input_tokens": sum(
                r["answer"]["model"]["input_tokens"] or 0 for r in rows.values() if "answer" in r
            ),
        }
        if arm != "baseline":
            scores[arm] = score
    complete = all(arms[a]["complete"] for a in ARMS)
    result: dict[str, Any] = {"arms": arms, "complete": complete}
    if scores:
        base = arms["baseline"].get("correct_pairs")
        best = select_candidate(scores)
        result["selected"] = best
        result["beats_baseline"] = base is not None and scores[best].correct > base
        result["selected_prompt_sha256"] = prompt_v3.sha256(best)
        result["note"] = (
            "Selection by the frozen rule of plan section 4. If beats_baseline is false the best "
            "candidate is still carried to the test phase. Structural pair checks do not rule out "
            "author bias. Human review was not conducted and is outside the scope of this "
            "experiment."
        )
    return result


def test_report(pairs: list[tuple[str, str]]) -> int:
    for arm in TEST_ARMS:
        rows = load_rows(raw_path(arm, "test"))
        status = {c: _status(r) for c, r in rows.items()}
        done = all(i in status for pair in pairs for i in pair)
        if done:
            score = score_arm(rows, pairs, prompt_chars(arm))
            print(f"{arm}: {score.correct}/{len(pairs)} correct pairs, {score.invalid} invalid")
        else:
            print(f"{arm}: {len(rows)}/{2 * len(pairs)} questions run")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.evaluation.run_false_premise", description=__doc__
    )
    parser.add_argument("--arm", choices=ARMS)
    parser.add_argument("--phase", choices=("dev", "test"), default="dev")
    parser.add_argument("--report", action="store_true", help="no API calls")
    args = parser.parse_args(argv)

    test = args.phase == "test"
    if test and args.arm not in (None, *TEST_ARMS):
        parser.error(f"the test phase runs only {TEST_ARMS}")
    cases, pairs = test_cases() if test else dev_cases()
    if args.arm and not args.report:
        problems = baseline_problems()
        if problems:
            print(f"REFUSING TO START: the pinned baseline differs: {problems}", file=sys.stderr)
            return 4
        try:
            prepared = prepare(DEFAULT_DATASET)
        except ExperimentError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        settings, ds = prepared.settings, prepared.dataset
        root = settings.data_dir / "live_work" / ra.REPO_NAME
        llm = get_llm_client(get_settings())
        try:
            print("preflight:", llm.preflight() or "ok")  # type: ignore[attr-defined]
        except LLMConfigError as e:
            print(f"STOPPED before any generation call: {e}", file=sys.stderr)
            return 4
        except LLMQuotaError as e:
            print(f"STOPPED: {e}", file=sys.stderr)
            return 3
        builder, store = load_builder(settings, ra.REPO_NAME, root, ds.commit)
        try:
            evidence = {
                c.id: builder.build_v2(c.question, TOP_K, True, EXTRA_CHUNKS) for c in cases
            }
            try:
                collect_condition(
                    cases, lambda c: evidence[c.id], make_service(args.arm, llm),
                    raw_path(args.arm, args.phase), False, args.arm,
                )  # fmt: skip
            except ra.QuotaExhausted as stop:
                print(
                    "\nSTOPPED: quota exhausted; run the same command again to resume.\n"
                    f"{str(stop)[:300]}",
                    file=sys.stderr,
                )
                return 3
        finally:
            close = getattr(store, "close", None)
            if close:
                close()

    if test:
        return test_report(pairs)
    result = report(pairs)
    result.update(
        plan="evaluation/plans/m15-false-premise.md",
        environment=environment(("qdrant-client",)),
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    )
    SUMMARY.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k not in ("environment",)}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
