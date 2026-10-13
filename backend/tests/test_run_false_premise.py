"""The M15 development-phase harness: cases, pair scoring, candidate selection, key preflight."""

import json
from pathlib import Path

import pytest
from answer_fixtures import FakeLLM, claim, make_evidence_v2, output

from app.answer.service import AnswerService
from app.evaluation import run_false_premise as fp
from app.llm.base import LLMConfigError
from app.llm.gemini import GeminiClient


def test_pair_scoring_rewards_discrimination_only() -> None:
    assert fp.pair_correct("insufficient_evidence", "answered")
    assert fp.pair_correct("insufficient_evidence", "partial")
    assert not fp.pair_correct("answered", "answered")  # answers the false premise
    assert not fp.pair_correct("insufficient_evidence", "insufficient_evidence")  # abstains on both
    assert not fp.pair_correct("answered", "insufficient_evidence")
    assert not fp.pair_correct("invalid_output", "answered")  # a format failure is a cost
    assert not fp.pair_correct("insufficient_evidence", "invalid_output")


def test_abstaining_everywhere_and_answering_everywhere_both_score_zero() -> None:
    pairs = [("p1", "q1"), ("p2", "q2"), ("p3", "q3")]
    abstain_all = {f"{x}{i}": "insufficient_evidence" for i in (1, 2, 3) for x in "pq"}
    answer_all = {f"{x}{i}": "answered" for i in (1, 2, 3) for x in "pq"}
    assert fp.correct_pairs(pairs, abstain_all) == 0 and fp.correct_pairs(pairs, answer_all) == 0
    mixed = {**answer_all, "p1": "insufficient_evidence", "p2": "insufficient_evidence"}
    assert fp.correct_pairs(pairs, mixed) == 2


def test_selection_prefers_correct_pairs_then_fewer_invalid_then_shorter_prompt() -> None:
    scores = {
        "c1": fp.ArmScore(correct=5, invalid=1, prompt_chars=900),
        "c2": fp.ArmScore(correct=6, invalid=2, prompt_chars=1200),
        "c3": fp.ArmScore(correct=6, invalid=1, prompt_chars=1500),
    }
    assert fp.select_candidate(scores) == "c3"  # ties on correct pairs broken by fewer invalid
    scores["c2"] = fp.ArmScore(correct=6, invalid=1, prompt_chars=1100)
    assert fp.select_candidate(scores) == "c2"  # then by the shorter prompt
    scores["c1"] = fp.ArmScore(correct=7, invalid=9, prompt_chars=5000)
    assert fp.select_candidate(scores) == "c1"  # correct pairs come first


def test_selection_is_deterministic_when_everything_ties() -> None:
    same = {n: fp.ArmScore(correct=3, invalid=0, prompt_chars=1000) for n in ("c3", "c1", "c2")}
    assert fp.select_candidate(same) == "c1"  # candidate order is the last tie-break


def test_dev_cases_are_the_eight_pairs_plus_the_three_known_failures() -> None:
    cases, pairs = fp.dev_cases()
    assert len(pairs) == 8 and len(cases) == 8 * 2 + 3
    assert len({c.id for c in cases}) == len(cases)
    assert {c.id for c in cases if c.set == "KNOWN"} == {"unans-08", "unans-14", "unans-11"}
    roles = {c.id: c.set for c in cases}
    assert all(roles[p] == "P" and roles[q] == "Q" for p, q in pairs)


def test_dev_cases_never_include_a_test_question() -> None:
    cases, _ = fp.dev_cases()
    root = Path(__file__).resolve().parents[2] / "evaluation" / "datasets"
    test = json.loads((root / "m15_pairs_test.json").read_text(encoding="utf-8"))["pairs"]
    test_questions = {x[k]["question"] for x in test for k in ("p", "q")}
    assert not {c.question for c in cases} & test_questions


def test_score_arm_counts_pairs_invalid_outputs_and_known_failures() -> None:
    cases, pairs = fp.dev_cases()
    status = {c.id: "answered" for c in cases}
    for p, _ in pairs[:3]:
        status[p] = "insufficient_evidence"  # 3 correct pairs
    status[pairs[3][1]] = "invalid_output"
    rows = {cid: {"case": {"id": cid}, "answer": {"status": s}} for cid, s in status.items()}
    score = fp.score_arm(rows, pairs, prompt_chars=1000)
    assert (score.correct, score.invalid) == (3, 1)
    assert fp.known_failures(rows) == {
        "unans-08": "answered",
        "unans-14": "answered",
        "unans-11": "answered",
    }


def test_test_cases_are_the_48_frozen_questions_and_disjoint_from_dev() -> None:
    cases, pairs = fp.test_cases()
    dev, _ = fp.dev_cases()
    assert len(pairs) == 24 and len(cases) == 48 and {c.set for c in cases} == {"P", "Q"}
    assert not {c.question for c in cases} & {c.question for c in dev}
    assert fp.raw_path("c1", "test").name == "m15_test_c1_raw.jsonl"
    assert fp.raw_path("c1").name == "m15_dev_c1_raw.jsonl"


# --- collection reuses the M14 machinery ---


def test_collection_is_resumable_and_stops_on_quota(tmp_path: Path) -> None:
    from app.evaluation import run_answers as ra
    from app.evaluation.run_answers_v2 import collect_condition, load_rows
    from app.llm.base import LLMQuotaError

    cases, _ = fp.dev_cases()
    raw = tmp_path / "raw.jsonl"
    llm = FakeLLM(output(claims=[claim("Saved.", "E1b")]))
    collect_condition(
        cases[:2], lambda c: make_evidence_v2(), AnswerService(None, llm), raw, False, "t"
    )
    assert len(load_rows(raw)) == 2
    collect_condition(
        cases[:3], lambda c: make_evidence_v2(), AnswerService(None, llm), raw, False, "t"
    )
    assert len(llm.requests) == 3
    quota = FakeLLM(LLMQuotaError("daily quota", None, 60.0))
    with pytest.raises(ra.QuotaExhausted):
        collect_condition(
            cases[3:5], lambda c: make_evidence_v2(), AnswerService(None, quota), raw, False, "t"
        )
    assert len(load_rows(raw)) == 3


# --- the credential preflight ---


class Transport:
    def __init__(self, status: int, body: bytes = b"{}") -> None:
        self.status, self.body, self.calls = status, body, []

    def __call__(self, url: str, headers: dict[str, str], body: bytes, timeout: float):
        self.calls.append((url, body))
        return self.status, self.body


def client(status: int, body: bytes = b"{}") -> tuple[GeminiClient, Transport]:
    transport = Transport(status, body)
    return GeminiClient(
        "FAKE-KEY-123", "gemini-3.5-flash-lite", transport, lambda s: None
    ), transport


def test_preflight_passes_on_200_with_one_metadata_request() -> None:
    gemini, transport = client(200, b'{"name": "models/gemini-3.5-flash-lite"}')
    assert gemini.preflight() == "models/gemini-3.5-flash-lite"
    assert len(transport.calls) == 1 and transport.calls[0][1] == b""  # a GET: no body
    assert transport.calls[0][0].endswith("/models/gemini-3.5-flash-lite")


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_preflight_fails_on_authentication_errors_without_the_key_in_the_message(
    status: int,
) -> None:
    gemini, _ = client(status, b'{"error": {"message": "API key not valid FAKE-KEY-123"}}')
    with pytest.raises(LLMConfigError) as raised:
        gemini.preflight()
    assert "FAKE-KEY-123" not in str(raised.value) and str(status) in str(raised.value)


def test_preflight_reports_a_quota_stop_distinctly() -> None:
    from app.llm.base import LLMQuotaError

    gemini, _ = client(
        429, b'{"error": {"details": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel"}]}}'
    )
    with pytest.raises(LLMQuotaError):
        gemini.preflight()


def test_preflight_is_not_retried() -> None:
    gemini, transport = client(503)
    with pytest.raises(LLMConfigError):
        gemini.preflight()
    assert len(transport.calls) == 1
