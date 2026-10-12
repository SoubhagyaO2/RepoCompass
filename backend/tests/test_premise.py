"""Contract v3 (Milestone 15): the premise/evidence-relation fields and validator v3, the prompt
candidates, and the v3 mode of the answer service. Plan sections 1 and 11."""

import hashlib
import json
from pathlib import Path

import pytest
from answer_fixtures import FakeLLM, claim, make_context, make_evidence

from app.answer import premise as pm
from app.answer.prompt import SYSTEM_PROMPT
from app.answer.service import AnswerService
from app.answer.validate import validate_output

CONTEXT = make_context()
ROOT = Path(__file__).resolve().parents[2]


def out3(
    premise: str = "the question assumes sessions are saved",
    supported: object = True,
    relation: object = "direct",
    status: object = "answered",
    claims: object = None,
    missing: object = "",
) -> str:
    if claims is None:
        claims = [claim("Sessions are saved by SessionInterface.save_session.", "E1")]
    return json.dumps(
        {
            "premise": premise,
            "premise_supported": supported,
            "evidence_relation": relation,
            "status": status,
            "claims": claims,
            "missing": missing,
        }
    )


def check(raw: str) -> tuple[bool, list[str]]:
    result, _ = pm.validate_output_v3(raw, CONTEXT)
    return result.valid, result.reasons


# --- valid combinations ---


def test_a_supported_direct_answer_is_valid_and_exposes_the_assessment() -> None:
    result, meta = pm.validate_output_v3(out3(), CONTEXT)
    assert result.valid and result.status == "answered"
    assert meta == pm.Assessment("the question assumes sessions are saved", True, "direct")


def test_partial_needs_direct_evidence_and_a_supported_premise() -> None:
    assert check(out3(status="partial", missing="No callers shown."))[0]


@pytest.mark.parametrize(
    ("supported", "relation"),
    [
        (False, "none"),
        (False, "related_only"),
        (True, "related_only"),
        (True, "none"),
        (True, "direct"),
    ],
)
def test_abstention_is_allowed_with_any_assessment(supported: bool, relation: str) -> None:
    raw = out3(supported=supported, relation=relation, status="insufficient_evidence",
               claims=[], missing="The evidence does not show the assumed feature.")  # fmt: skip
    assert check(raw) == (True, [])


# --- the consistency rules (plan section 1, rules 2-4) ---


@pytest.mark.parametrize("status", ["answered", "partial"])
@pytest.mark.parametrize("relation", ["related_only", "none"])
def test_claims_need_direct_evidence(status: str, relation: str) -> None:
    valid, reasons = check(out3(status=status, relation=relation, missing="gap"))
    assert not valid and any("evidence_relation" in r for r in reasons)


@pytest.mark.parametrize("status", ["answered", "partial"])
def test_an_unsupported_premise_cannot_be_answered(status: str) -> None:
    valid, reasons = check(out3(status=status, supported=False, missing="gap"))
    assert not valid and any("premise_supported" in r for r in reasons)


def test_an_unsupported_premise_with_claims_is_rejected_by_the_inherited_rules() -> None:
    raw = out3(supported=False, relation="none", status="insufficient_evidence", missing="x")
    valid, reasons = check(raw)  # status ok for v3, but insufficient_evidence must have no claims
    assert not valid and any("needs no claims" in r for r in reasons)


# --- malformed outputs ---


@pytest.mark.parametrize(
    ("name", "raw", "expected"),
    [
        ("not json", "{oops", "not a single JSON object"),
        ("text around", "Sure: " + out3(), "not a single JSON object"),
        (
            "v1 contract only",
            json.dumps({"status": "answered", "claims": [], "missing": ""}),
            "fields must be exactly",
        ),
        (
            "extra field",
            json.dumps({**json.loads(out3()), "confidence": 1}),
            "fields must be exactly",
        ),
        (
            "missing premise",
            json.dumps({k: v for k, v in json.loads(out3()).items() if k != "premise"}),
            "fields must be exactly",
        ),
        ("empty premise", out3(premise="   "), "premise must be a non-empty string"),
        ("premise not a string", out3(premise=3), "premise must be a non-empty string"),
        ("supported as a string", out3(supported="true"), "premise_supported must be a boolean"),
        ("supported as an int", out3(supported=1), "premise_supported must be a boolean"),
        ("unknown relation", out3(relation="maybe"), "evidence_relation must be one of"),
        ("relation not a string", out3(relation=True), "evidence_relation must be one of"),
    ],
)
def test_malformed_v3_outputs_are_rejected(name: str, raw: str, expected: str) -> None:
    valid, reasons = check(raw)
    assert not valid, name
    assert any(expected in r for r in reasons), (name, reasons)


def test_a_json_list_is_rejected() -> None:
    assert not check("[1, 2]")[0]


# --- the unchanged v1 rules still apply underneath ---


def test_citation_rules_are_inherited() -> None:
    valid, reasons = check(out3(claims=[claim("Saved.", "E99")]))
    assert not valid and any("E99" in r for r in reasons)
    valid, reasons = check(out3(claims=[claim("It lives in src/pkg/other.py.", "E1")]))
    assert not valid and any("other.py" in r for r in reasons)


def test_the_v1_validator_is_untouched_and_rejects_v3_outputs() -> None:
    assert not validate_output(out3(), CONTEXT).valid  # extra fields: it is the baseline contract
    source = (
        (ROOT / "backend" / "app" / "answer" / "validate.py").read_bytes().replace(b"\r\n", b"\n")
    )
    assert hashlib.sha256(source).hexdigest() == (
        "909ab2133d33cfe5c6d676df5fe173f1b48b003f4ae60365956955d5241a5322"
    )


# --- prompt candidates ---


def test_there_are_at_most_three_distinct_candidates() -> None:
    from app.answer import prompt_v3 as p3

    assert list(p3.CANDIDATES) == ["c1", "c2", "c3"]
    assert len(set(p3.CANDIDATES.values())) == 3 and SYSTEM_PROMPT not in p3.CANDIDATES.values()


def test_every_candidate_keeps_the_frozen_rules_and_states_the_contract() -> None:
    from app.answer import prompt_v3 as p3

    base = SYSTEM_PROMPT.split("\n7. Respond")[0]
    for name, text in p3.CANDIDATES.items():
        assert text.startswith(base), name  # rules 1-6 verbatim
        for field in (
            "premise",
            "premise_supported",
            "evidence_relation",
            "status",
            "claims",
            "missing",
        ):
            assert f'"{field}"' in text, (name, field)
        for value in ("direct", "related_only", "none", "insufficient_evidence"):
            assert value in text, (name, value)
        assert p3.sha256(name) == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_candidates_do_not_quote_any_benchmark_or_pair_question() -> None:
    from app.answer import prompt_v3 as p3

    banned = [
        json.loads(f.read_text(encoding="utf-8"))
        for f in (ROOT / "evaluation" / "datasets").glob("m15_pairs_*.json")
    ]
    questions = [x[k]["question"].lower() for d in banned for x in d["pairs"] for k in ("p", "q")]
    for name, text in p3.CANDIDATES.items():
        assert not any(q in text.lower() for q in questions), name
        assert "migration" not in text.lower() and "admin" not in text.lower(), (
            name
        )  # known failures


# --- the v3 mode of the answer service ---


def v3_service(reply: str) -> tuple[AnswerService, FakeLLM]:
    from app.answer import prompt_v3 as p3

    llm = FakeLLM(reply)
    return AnswerService(None, llm, system_prompt=p3.CANDIDATES["c1"], contract="v3"), llm


def test_v3_service_returns_the_assessment_and_the_candidate_hash() -> None:
    from app.answer import prompt_v3 as p3

    service, llm = v3_service(out3())
    answer = service.answer_from_evidence("Where are sessions saved?", make_evidence())
    assert answer.status == "answered" and answer.validation.valid
    assert (answer.premise, answer.premise_supported, answer.evidence_relation) == (
        "the question assumes sessions are saved", True, "direct",
    )  # fmt: skip
    assert answer.prompt_sha256 == p3.sha256("c1") and llm.requests[0].system == p3.CANDIDATES["c1"]


def test_v3_service_turns_an_inconsistent_output_into_invalid_output() -> None:
    service, _ = v3_service(out3(relation="related_only"))
    answer = service.answer_from_evidence("q", make_evidence())
    assert answer.status == "invalid_output" and answer.premise is None
    assert any("evidence_relation" in r for r in answer.validation.reasons)


def test_the_default_service_is_unchanged() -> None:
    from answer_fixtures import output

    llm = FakeLLM(output())
    answer = AnswerService(None, llm).answer_from_evidence("q", make_evidence())
    assert answer.status == "answered" and answer.premise is None
    assert llm.requests[0].system == SYSTEM_PROMPT
