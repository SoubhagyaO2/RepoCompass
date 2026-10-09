"""The M13 evaluation harness: the case plan is deterministic and matches the frozen plan."""

import json
from collections import Counter
from pathlib import Path

import pytest
from answer_fixtures import FakeLLM, claim, make_evidence, output

from app.evaluation import run_answers as ra

A = [(f"flask-{i:03d}", f"question {i}?") for i in range(1, 74)]
B = [(f"unans-{i:02d}", f"unanswerable {i}?") for i in range(1, 16)]
ROOT = Path(__file__).resolve().parents[2]


def test_plan_has_the_frozen_set_sizes() -> None:
    cases = ra.plan_cases(A, B)
    assert Counter(c.set for c in cases) == {"A": 73, "B": 15, "C": 20, "D": 5}
    assert len(cases) == 113 and len({c.id for c in cases}) == 113


def test_plan_is_deterministic() -> None:
    assert ra.plan_cases(A, B) == ra.plan_cases(A, B)


def test_set_c_pairs_each_question_with_other_questions_evidence() -> None:
    cases = [c for c in ra.plan_cases(A, B) if c.set == "C"]
    assert all(c.evidence_from != c.id.removeprefix("C:") for c in cases)  # no fixed point
    sources = [c.id.removeprefix("C:") for c in cases]
    assert sorted(c.evidence_from for c in cases) == sorted(sources)  # a permutation
    assert {c.question for c in cases} == {dict(A)[s] for s in sources}


def test_set_d_plants_one_distinct_injection_per_case_in_a_copy() -> None:
    cases = [c for c in ra.plan_cases(A, B) if c.set == "D"]
    assert sorted(c.injection for c in cases) == [0, 1, 2, 3, 4]
    evidence = make_evidence()
    original = evidence.evidence[0].text
    planted = ra.plant(evidence, 2)
    assert evidence.evidence[0].text == original  # the original is untouched
    assert planted.evidence[0].text.endswith(ra.INJECTIONS[2][0])
    assert planted.evidence[1].text == evidence.evidence[1].text


def test_derangement_has_no_fixed_points_and_is_reproducible() -> None:
    items = [str(i) for i in range(20)]
    d = ra.derangement(items, 0)
    assert sorted(d) == sorted(items) and all(a != b for a, b in zip(items, d, strict=True))
    assert d == ra.derangement(items, 0)


def test_unanswerable_dataset_matches_the_plan() -> None:
    data = json.loads(ra.UNANSWERABLE.read_text(encoding="utf-8"))
    assert len(data["questions"]) == 15
    assert len({q["id"] for q in data["questions"]}) == 15


def row(case_id: str, set_name: str, status: str, valid: bool = True) -> dict:
    validation = {"valid": valid, "reasons": [], "paths_only_in_uncited_items": 0}
    return {
        "case": {"id": case_id, "set": set_name, "injection": None},
        "answer": {
            "status": status, "validation": validation, "claims": [], "items_omitted": 0,
            "model": {}, "raw_output": "", "missing": "",
        },
    }  # fmt: skip


def test_review_selection_follows_the_plan() -> None:
    rows = {f"a{i}": row(f"a{i}", "A", "answered") for i in range(40)}
    rows["a_bad"] = row("a_bad", "A", "invalid_output", valid=False)
    rows["b1"] = row("b1", "B", "answered")
    rows["b2"] = row("b2", "B", "insufficient_evidence")
    rows["c1"] = row("c1", "C", "partial")
    rows["d1"] = row("d1", "D", "invalid_output", valid=False)
    selection = ra.review_selection(rows)
    assert len(selection["set_a_sample"]) == 25 and "a_bad" not in selection["set_a_sample"]
    assert selection["others"] == ["b1", "c1"]  # only answered/partial of B, C, D
    assert ra.review_selection(rows) == selection  # seeded


class _Builder:
    def build(self, question: str, top_k: int, include_text: bool = False):
        return make_evidence(question)


def _quota_llm() -> FakeLLM:
    from app.llm.base import LLMQuotaError

    return FakeLLM(LLMQuotaError("daily quota used up", None, 7200.0))


def test_the_run_stops_on_quota_exhaustion_without_writing_a_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.answer.service import AnswerService

    monkeypatch.setattr(ra, "RAW", tmp_path / "raw.jsonl")
    cases = ra.plan_cases(A, B)[:3]
    with pytest.raises(ra.QuotaExhausted) as stop:
        ra.collect(_Builder(), cases, AnswerService(None, _quota_llm()))  # type: ignore[arg-type]
    assert stop.value.retry_after_s == 7200.0
    assert not (tmp_path / "raw.jsonl").exists()


def test_the_run_resumes_and_skips_finished_cases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.answer.service import AnswerService

    monkeypatch.setattr(ra, "RAW", tmp_path / "raw.jsonl")
    cases = ra.plan_cases(A, B)[:3]
    llm = FakeLLM(output(claims=[claim("Saved by SessionInterface.save_session.", "E1")]))
    ra.collect(_Builder(), cases[:2], AnswerService(None, llm))  # type: ignore[arg-type]
    assert len(llm.requests) == 2
    ra.collect(_Builder(), cases, AnswerService(None, llm))  # type: ignore[arg-type]
    assert len(llm.requests) == 3  # only the third case was run
    lines = (tmp_path / "raw.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
