"""The M14 analysis and decision rule (plan sections 4-7), tested as pure functions."""

import hashlib
import json
from math import comb
from pathlib import Path

import pytest

from app.evaluation import run_answers_v2 as r2
from app.evaluation.run_answers_v2 import Comparison, decide, mcnemar_exact, paired_counts

BASE_VERSION = "gemini-3.5-flash-lite"


# --- exact McNemar --------------------------------------------------------------------------------


def test_mcnemar_matches_the_binomial_definition() -> None:
    assert mcnemar_exact(0, 0) == 1.0
    assert mcnemar_exact(6, 0) == pytest.approx(2 * 0.5**6)  # 0.03125
    assert mcnemar_exact(5, 0) == pytest.approx(2 * 0.5**5)  # 0.0625
    n = 9
    assert mcnemar_exact(8, 1) == pytest.approx(2 * sum(comb(n, k) for k in range(2)) / 2**n)
    assert mcnemar_exact(3, 3) == 1.0
    assert mcnemar_exact(2, 7) == mcnemar_exact(7, 2)  # symmetric, two-sided


def test_the_power_note_of_the_plan_is_true() -> None:
    """With c = 0 an exact test needs b >= 6; with c = 1, b >= 8 (plan section 5)."""
    assert mcnemar_exact(6, 0) < 0.05 <= mcnemar_exact(5, 0)
    assert mcnemar_exact(8, 1) < 0.05 <= mcnemar_exact(7, 1)


# --- paired counts and transitions ---


def statuses(**by_id: str) -> dict[str, str]:
    return dict(by_id)


def test_paired_counts_follow_the_definition_b_and_c() -> None:
    old = statuses(
        q1="insufficient_evidence",
        q2="insufficient_evidence",
        q3="answered",
        q4="partial",
        q5="insufficient_evidence",
        q6="answered",
    )
    new = statuses(
        q1="answered",
        q2="partial",
        q3="insufficient_evidence",
        q4="partial",
        q5="insufficient_evidence",
        q6="answered",
    )
    cmp = paired_counts(new, old)
    assert (cmp.b, cmp.c) == (2, 1)  # q1,q2 stopped abstaining; q3 started
    assert cmp.excluded == []
    assert cmp.p == mcnemar_exact(2, 1)


def test_invalid_outputs_are_excluded_from_b_and_c_and_listed() -> None:
    old = statuses(q1="insufficient_evidence", q2="answered", q3="invalid_output", q4="answered")
    new = statuses(q1="invalid_output", q2="insufficient_evidence", q3="answered", q4="answered")
    cmp = paired_counts(new, old)
    assert (cmp.b, cmp.c) == (0, 1)
    assert cmp.excluded == ["q1", "q3"]


def test_the_two_runs_must_cover_the_same_questions() -> None:
    with pytest.raises(ValueError):
        paired_counts(statuses(q1="answered"), statuses(q1="answered", q2="answered"))


def test_transition_table_counts_every_pair() -> None:
    old = statuses(q1="answered", q2="answered", q3="insufficient_evidence")
    new = statuses(q1="answered", q2="partial", q3="answered")
    table = r2.transition_table(old, new)
    assert table["answered"]["answered"] == 1 and table["answered"]["partial"] == 1
    assert table["insufficient_evidence"]["answered"] == 1
    assert sum(sum(row.values()) for row in table.values()) == 3
    assert set(table) == set(r2.STATUSES) and all(
        set(row) == set(r2.STATUSES) for row in table.values()
    )


# --- the decision rule (plan section 7) ---


def cmp(b: int, c: int, excluded: int = 0) -> Comparison:
    return Comparison(b=b, c=c, p=mcnemar_exact(b, c), excluded=[f"x{i}" for i in range(excluded)])


def ok_inputs(**override):
    base = dict(
        complete=True,
        model_versions={BASE_VERSION},
        baseline_version=BASE_VERSION,
        hours_apart=5.0,
        model_unavailable=False,
        a=cmp(8, 0),
        b=cmp(7, 0),
        n=cmp(1, 0),
        guards_ok=True,
        newly_answered_failures=[],
    )
    base.update(override)
    return base


def test_supported_when_everything_holds() -> None:
    d = decide(**ok_inputs())
    assert d.category == "supported" and d.reasons == []


def test_not_supported_when_neither_comparison_is_significant() -> None:
    d = decide(**ok_inputs(a=cmp(3, 1), b=cmp(2, 2)))
    assert d.category == "not_supported"


def test_not_supported_when_the_difference_is_in_the_wrong_direction() -> None:
    d = decide(**ok_inputs(a=cmp(0, 7), b=cmp(0, 6)))  # significant, but abstentions went UP
    assert d.category == "not_supported"


def test_not_supported_when_a_newly_answered_case_fails_items_1_or_2() -> None:
    d = decide(**ok_inputs(newly_answered_failures=["flask-004"]))
    assert d.category == "not_supported"
    assert any("flask-004" in r for r in d.reasons)


def test_not_supported_when_a_guard_fails() -> None:
    d = decide(**ok_inputs(guards_ok=False))
    assert d.category == "not_supported" and any("guard" in r for r in d.reasons)


def test_inconclusive_when_a_call_is_missing() -> None:
    assert decide(**ok_inputs(complete=False)).category == "inconclusive"


@pytest.mark.parametrize("versions", [{"gemini-3.8-flash"}, {BASE_VERSION, "gemini-3.8-flash"}])
def test_inconclusive_when_the_served_model_changed(versions: set[str]) -> None:
    assert decide(**ok_inputs(model_versions=versions)).category == "inconclusive"


def test_inconclusive_when_too_many_questions_are_excluded() -> None:
    assert decide(**ok_inputs(a=cmp(8, 0, excluded=8))).category == "inconclusive"
    assert decide(**ok_inputs(b=cmp(7, 0, excluded=9))).category == "inconclusive"
    assert decide(**ok_inputs(a=cmp(8, 0, excluded=7))).category == "supported"  # 7 is below 8


def test_inconclusive_when_the_runs_were_far_apart_or_the_model_vanished() -> None:
    assert decide(**ok_inputs(hours_apart=72.1)).category == "inconclusive"
    assert decide(**ok_inputs(hours_apart=72.0)).category == "supported"  # exactly 72 h is allowed
    assert decide(**ok_inputs(model_unavailable=True)).category == "inconclusive"


def test_inconclusive_when_the_noise_check_shows_too_much_variation() -> None:
    assert decide(**ok_inputs(n=cmp(3, 3))).category == "inconclusive"  # b_N + c_N = 6
    assert decide(**ok_inputs(n=cmp(3, 2))).category == "supported"  # 5 is below 6


def test_inconclusive_when_the_gain_over_baseline_is_not_seen_against_the_repeat() -> None:
    d = decide(**ok_inputs(a=cmp(8, 0), b=cmp(3, 2)))
    assert d.category == "inconclusive" and any("repeat" in r for r in d.reasons)


def test_inconclusive_when_only_the_repeat_comparison_is_significant() -> None:
    d = decide(**ok_inputs(a=cmp(3, 2), b=cmp(7, 0)))
    assert d.category == "inconclusive"


def test_inconclusive_wins_over_not_supported_and_supported() -> None:
    d = decide(**ok_inputs(complete=False, guards_ok=False, newly_answered_failures=["x"]))
    assert d.category == "inconclusive"


def test_every_category_is_reachable() -> None:
    seen = {
        decide(**ok_inputs()).category,
        decide(**ok_inputs(a=cmp(1, 1), b=cmp(1, 1))).category,
        decide(**ok_inputs(complete=False)).category,
    }
    assert seen == {"supported", "not_supported", "inconclusive"}


# --- baseline immutability (plan section 11) ---


def test_the_pinned_hashes_match_the_plan_and_the_files(tmp_path: Path) -> None:
    plan = (
        Path(__file__).resolve().parents[2] / "evaluation/plans/m14-additional-chunks.md"
    ).read_text(encoding="utf-8")
    for relative, digest in r2.PINNED_HASHES.items():
        assert digest in plan, relative
    assert r2.baseline_problems() == []


def test_baseline_problems_names_a_changed_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = tmp_path / "b.json"
    f.write_text("original\r\n", encoding="utf-8", newline="")
    good = hashlib.sha256(b"original\n").hexdigest()  # CRLF and LF checkouts hash the same
    monkeypatch.setattr(r2, "PINNED_HASHES", {"b.json": good})
    monkeypatch.setattr(r2, "REPO_ROOT", tmp_path)
    assert r2.baseline_problems() == []
    f.write_text("tampered\n", encoding="utf-8", newline="")
    assert r2.baseline_problems() == ["b.json"]
    (tmp_path / "b.json").unlink()
    assert r2.baseline_problems() == ["b.json"]


# --- review selection (plan section 6) ---


def row(case_id: str, set_name: str, status: str, valid: bool = True) -> dict:
    return {
        "case": {"id": case_id, "set": set_name, "injection": None},
        "answer": {"status": status, "validation": {"valid": valid}},
    }


def test_newly_answered_are_the_baseline_abstentions_that_now_answer() -> None:
    base = {
        f"flask-{i:03d}": row(f"flask-{i:03d}", "A", s)
        for i, s in enumerate(
            [
                "insufficient_evidence",
                "insufficient_evidence",
                "answered",
                "partial",
                "insufficient_evidence",
            ],
            1,
        )
    }
    new = {
        k: row(k, "A", s)
        for k, s in zip(
            base,
            ["answered", "partial", "answered", "insufficient_evidence", "insufficient_evidence"],
            strict=True,
        )
    }
    assert r2.newly_answered(base, new) == ["flask-001", "flask-002"]


def test_human_subset_is_all_if_ten_or_fewer_else_a_reproducible_sample_of_ten() -> None:
    few = [f"flask-{i:03d}" for i in range(1, 8)]
    assert r2.human_subset(few) == few
    many = [f"flask-{i:03d}" for i in range(1, 30)]
    pick = r2.human_subset(many)
    assert (
        len(pick) == 10 and set(pick) <= set(many) and r2.human_subset(many[::-1]) == pick
    )  # order-free


def test_review_selection_has_the_three_parts_of_the_plan() -> None:
    base = {
        f"flask-{i:03d}": row(
            f"flask-{i:03d}", "A", "insufficient_evidence" if i <= 4 else "answered"
        )
        for i in range(1, 41)
    }
    new = {k: row(k, "A", "answered") for k in base}
    new["flask-040"] = row("flask-040", "A", "invalid_output", valid=False)
    new["b1"] = row("b1", "B", "answered")
    new["c1"] = row("c1", "C", "insufficient_evidence")
    new["d1"] = row("d1", "D", "partial")
    sel = r2.review_selection(base, new)
    assert sel["newly_answered"] == ["flask-001", "flask-002", "flask-003", "flask-004"]
    assert len(sel["other_sample"]) == 15 and not set(sel["other_sample"]) & set(
        sel["newly_answered"]
    )
    assert "flask-040" not in sel["other_sample"]  # only valid answers are sampled
    assert sel["others"] == ["b1", "d1"] and sel["human"] == sel["newly_answered"]
    assert r2.review_selection(base, new) == sel


# --- failures in the reviewed labels (decision condition 4) ---


def test_newly_answered_failures_look_at_items_1_and_2_of_every_reader() -> None:
    labels = {
        "reviewer1": {
            "flask-001": {"1": "PASS", "2": "PASS"},
            "flask-002": {"1": "FAIL", "2": "PASS"},
        },
        "human": {
            "flask-001": {"1": "PASS", "2": "FAIL"},
            "flask-003": {"1": "UNCERTAIN", "2": "UNCERTAIN"},
        },
    }
    failures = r2.newly_answered_failures(labels, ["flask-001", "flask-002", "flask-003"])
    assert failures == ["flask-001", "flask-002"]  # UNCERTAIN never counts as a failure


# --- context diagnostic (no model) ---


def test_context_diagnostic_counts_questions_whose_context_has_the_named_symbol() -> None:
    from answer_fixtures import make_evidence_v2

    ev = make_evidence_v2()
    hit = [{"file": "src/pkg/sessions.py", "name": "SessionInterface.open_session"}]
    miss = [{"file": "src/pkg/sessions.py", "name": "SessionInterface.nowhere"}]
    other_file = [{"file": "src/pkg/app.py", "name": "SessionInterface.open_session"}]
    assert r2.context_has_symbol(ev, hit, include_additional=True)
    assert not r2.context_has_symbol(
        ev, hit, include_additional=False
    )  # v1 view: primary chunks only
    assert not r2.context_has_symbol(ev, miss, include_additional=True)
    assert not r2.context_has_symbol(ev, other_file, include_additional=True)
    assert r2.context_has_symbol(ev, hit + miss, include_additional=True)  # any one is enough


def test_json_files_written_by_the_runner_use_new_names_only() -> None:
    for name in (r2.V2_RAW, r2.REPEAT_RAW, r2.V2_EVIDENCE, r2.V2_SUMMARY):
        assert "answers_flask" not in name.name  # never the M13 baseline files
    json.dumps({"ok": True})


# --- the pipeline end to end with a fake model --------------------------------------


def _cases():
    from app.evaluation import run_answers as ra

    a = [(f"flask-{i:03d}", f"question {i}?") for i in range(1, 74)]
    b = [(f"unans-{i:02d}", f"unanswerable {i}?") for i in range(1, 16)]
    return ra.plan_cases(a, b)


def test_collect_condition_appends_rows_with_context_stats_and_resumes(
    tmp_path: Path,
) -> None:
    from answer_fixtures import FakeLLM, claim, make_evidence_v2, output

    from app.answer.service import AnswerService

    raw = tmp_path / "raw.jsonl"
    cases = _cases()[:3]
    llm = FakeLLM(output(claims=[claim("Saved.", "E1b")]))
    r2.collect_condition(
        cases, lambda c: make_evidence_v2(), AnswerService(None, llm), raw, False, "t"
    )
    rows = r2.load_rows(raw)
    assert len(rows) == 3 and len(llm.requests) == 3
    first = rows[cases[0].id]
    assert first["context"]["blocks"] == 6 and first["context"]["omitted"] == 0
    assert first["context"]["additional_chunks_per_file"] == [2, 1, 0]
    assert (
        "started_at" in first
        and first["answer"]["claims"][0]["citations"][0]["evidence_id"] == "E1b"
    )
    r2.collect_condition(
        cases, lambda c: make_evidence_v2(), AnswerService(None, llm), raw, False, "t"
    )
    assert len(llm.requests) == 3  # nothing run twice


def test_a_quota_stop_writes_no_row_and_resumes(tmp_path: Path) -> None:
    from answer_fixtures import FakeLLM, make_evidence_v2

    from app.answer.service import AnswerService
    from app.evaluation import run_answers as ra
    from app.llm.base import LLMQuotaError

    raw = tmp_path / "raw.jsonl"
    llm = FakeLLM(LLMQuotaError("daily quota", None, 3600.0))
    with pytest.raises(ra.QuotaExhausted):
        r2.collect_condition(
            _cases()[:2], lambda c: make_evidence_v2(), AnswerService(None, llm), raw, False, "t"
        )
    assert not raw.exists()


def _row(case, status: str, version: str = BASE_VERSION, when: str = "2026-10-11T10:00:00+00:00"):
    valid = status != "invalid_output"
    body = {
        "status": status, "missing": "", "claims": [], "items_omitted": 0, "raw_output": "{}",
        "validation": {"valid": valid, "reasons": [], "paths_only_in_uncited_items": 0},
        "model": {"requested_model": BASE_VERSION, "returned_model_version": version,
                  "reasoning_setting": "provider default", "input_tokens": 100, "output_tokens": 5,
                  "thinking_tokens": 0, "attempts": 1, "retries": 0, "errors": [],
                  "estimated_cost_usd": 0.0, "cost_basis": "free", "latency_ms": 1000.0},
    }  # fmt: skip
    return {
        "case": case.__dict__,
        "started_at": when,
        "context": {
            "chars": 5000,
            "blocks": 6,
            "omitted": 0,
            "additional_chunks_per_file": [2, 2, 2],
        },
        "answer": body,
    }


def test_analyse_computes_the_comparisons_the_decision_and_the_sheets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from answer_fixtures import make_evidence_v2

    cases = _cases()
    a_ids = [c.id for c in cases if c.set == "A"]
    base = {
        c.id: _row(c, "insufficient_evidence" if c.id in a_ids[:8] else "answered") for c in cases
    }
    new = {c.id: _row(c, "answered") for c in cases}  # all eight abstentions now answer
    repeat = {c.id: _row(c, "insufficient_evidence" if c.id in a_ids[:8] else "answered")
              for c in cases if c.set == "A"}  # fmt: skip
    evidence = {c.evidence_from: make_evidence_v2() for c in cases if c.set in ("A", "B")}
    symbol = {"file": "src/pkg/sessions.py", "name": "SessionInterface.open_session"}
    questions = {i: {"relevant_symbols": [symbol]} for i in a_ids}  # fmt: skip
    result = r2.analyse(
        cases, base, new, repeat, evidence, {"responses": 88, "violations": {}}, questions
    )
    assert result["complete"] is True
    assert result["comparisons"]["A"]["b"] == 8 and result["comparisons"]["A"]["c"] == 0
    assert result["comparisons"]["B"]["b"] == 8 and result["comparisons"]["N"]["b"] == 0
    assert result["decision"]["category"] == "supported"  # preliminary: no labels yet
    assert result["decision_is_final"] is False and result["human_review"] == "NOT completed"
    assert result["review_selection"]["newly_answered"] == sorted(a_ids[:8])
    assert result["context_diagnostic"]["v2_context_has_symbol"] == 73
    assert result["context_diagnostic"]["v1_context_has_symbol"] == 0
    assert result["transitions"]["baseline_to_v2"]["insufficient_evidence"]["answered"] == 8
    assert set(result["set_a_changes"]) == set(a_ids[:8])

    monkeypatch.setattr(r2, "SHEET_R1", tmp_path / "r1.md")
    monkeypatch.setattr(r2, "SHEET_HUMAN", tmp_path / "human.md")
    monkeypatch.setattr(r2, "V2_SELECTION", tmp_path / "sel.json")
    r2.write_sheets(new, evidence, result["review_selection"])
    human = (tmp_path / "human.md").read_text(encoding="utf-8")
    assert (
        human.count("\n## ") + human.startswith("## ") <= 8
    )  # the human sheet holds only the subset
    assert "Everything the model was given" not in human  # these answers are `answered`
    assert "PASS / FAIL / UNCERTAIN" in human


def test_an_incomplete_run_has_no_decision() -> None:
    cases = _cases()
    new = {c.id: _row(c, "answered") for c in cases[:10]}
    result = r2.analyse(cases, {}, new, {}, {}, {"responses": 0, "violations": {}}, {})
    assert result["complete"] is False and result["decision"] is None
