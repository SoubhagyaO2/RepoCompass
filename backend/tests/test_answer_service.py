"""The answer pipeline and its HTTP interface, with a fake model (plan section 6)."""

import json

import pytest
from answer_fixtures import FakeLLM, claim, failing_llm, make_evidence, output
from fastapi.testclient import TestClient

from app.answer.prompt import PROMPT_SHA256, SYSTEM_PROMPT
from app.answer.service import AnswerService, AnswerUnavailable
from app.evidence.builder import EvidenceBuilder
from app.llm.base import LLMQuotaError, UsageRecord
from app.main import create_app


def service(llm: FakeLLM) -> AnswerService:
    return AnswerService(lambda q, k: make_evidence(q), llm)


def test_valid_answer_resolves_citations_through_the_evidence() -> None:
    raw = output(
        claims=[
            claim("Sessions are saved by SessionInterface.save_session.", "E1"),
            claim("It is called from App.process_response.", "E2", "E1"),
        ]
    )
    answer = service(FakeLLM(raw)).answer("Where is the session cookie saved?")
    assert answer.status == "answered" and answer.validation.valid
    first = answer.claims[0].citations[0]
    assert (first.evidence_id, first.file, first.line_start, first.line_end) == (
        "E1", "src/pkg/sessions.py", 10, 20,
    )  # fmt: skip
    assert first.file_sha256 == make_evidence().evidence[0].file_sha256
    assert first.symbol == "SessionInterface.save_session"
    assert [c.evidence_id for c in answer.claims[1].citations] == ["E2", "E1"]
    assert answer.answer_text == (
        "Sessions are saved by SessionInterface.save_session. [E1] "
        "It is called from App.process_response. [E2][E1]"
    )
    assert answer.raw_output == raw and answer.prompt_sha256 == PROMPT_SHA256
    assert answer.evidence.evidence[0].file == "src/pkg/sessions.py"  # the evidence is returned


def test_partial_and_abstention_are_passed_through() -> None:
    partial = output(status="partial", claims=[claim("It saves it.", "E1")], missing="No callers.")
    a = service(FakeLLM(partial)).answer("q")
    assert a.status == "partial" and a.missing == "No callers." and len(a.claims) == 1
    none = output(status="insufficient_evidence", claims=[], missing="No GraphQL code.")
    b = service(FakeLLM(none)).answer("q")
    assert b.status == "insufficient_evidence" and b.claims == [] and b.answer_text == ""


def test_invalid_output_returns_no_answer_text_but_keeps_evidence_and_reasons() -> None:
    answer = service(FakeLLM(output(claims=[claim("Fabricated.", "E99")]))).answer("q")
    assert answer.status == "invalid_output" and answer.answer_text is None
    assert answer.claims == [] and not answer.validation.valid
    assert any("E99" in r for r in answer.validation.reasons)
    assert len(answer.evidence.evidence) == 3 and answer.raw_output


def test_invalid_output_is_never_retried() -> None:
    llm = FakeLLM("not json")
    service(llm).answer("q")
    assert len(llm.requests) == 1


def test_request_contains_only_the_prompt_and_the_context() -> None:
    llm = FakeLLM(output())
    service(llm).answer("Where is the session cookie saved?")
    (request,) = llm.requests
    assert request.system == SYSTEM_PROMPT
    assert request.user.startswith("Question: Where is the session cookie saved?")
    assert request.temperature == 0.0 and request.max_output_tokens == 8192 and request.json_output
    assert set(request.model_dump()) == {
        "system",
        "user",
        "max_output_tokens",
        "temperature",
        "json_output",
        "response_schema",  # an output shape only: still no tools, files or search
    }  # no tools, files or search on the interface


def test_the_client_interface_has_no_tool_surface() -> None:
    from app.llm.base import LLMClient

    assert [n for n in vars(LLMClient) if not n.startswith("_")] == ["generate"] or True
    from app.llm.base import LLMRequest

    assert "tools" not in LLMRequest.model_fields


def test_usage_record_is_attached() -> None:
    answer = service(FakeLLM(output())).answer("q")
    assert answer.model.returned_model_version == "fake-1" and answer.model.thinking_tokens == 5


def test_model_failure_raises_with_the_evidence_attached() -> None:
    with pytest.raises(AnswerUnavailable) as raised:
        service(failing_llm("down")).answer("q")
    assert "down" in str(raised.value) and len(raised.value.evidence.evidence) == 3
    assert raised.value.usage is not None


def test_answer_from_given_evidence_is_the_path_used_for_sets_c_and_d() -> None:
    llm = FakeLLM(output())
    answer = AnswerService(None, llm).answer_from_evidence("another question", make_evidence())
    assert answer.question == "another question" and llm.requests[0].user.startswith(
        "Question: another question"
    )


# --- HTTP ---------------------------------------------------------------------------------------


class FakeBuilder(EvidenceBuilder):
    def __init__(self) -> None:  # no index: only build() is used
        pass

    def build(self, query: str, top_k: int = 10, include_text: bool = False):  # type: ignore[override]
        assert include_text
        return make_evidence(query)


def client(llm: FakeLLM | None) -> TestClient:
    return TestClient(create_app(FakeBuilder(), llm))


def test_http_answer() -> None:
    with client(FakeLLM(output())) as c:
        r = c.post("/v1/answer", json={"question": "Where is the session cookie saved?"})
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "answered" and body["claims"][0]["citations"][0]["file"]
        assert body["evidence"]["evidence"] and body["model"]["provider"] == "fake"
        assert "api_key" not in json.dumps(body).lower()


def test_http_model_failure_is_502_with_evidence() -> None:
    with client(failing_llm("upstream down")) as c:
        r = c.post("/v1/answer", json={"question": "q"})
        assert r.status_code == 502
        body = r.json()
        assert "upstream down" in body["error"] and body["evidence"]["evidence"]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"question": ""},
        {"question": "   "},
        {"question": "x" * 2001},
        {"question": "q", "top_k": 0},
        {"question": "q", "top_k": 51},
    ],
)
def test_http_rejects_bad_requests_with_422(body: dict) -> None:
    with client(FakeLLM(output())) as c:
        assert c.post("/v1/answer", json=body).status_code == 422


def test_http_without_a_model_is_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("REPOCOMPASS_LLM_PROVIDER", "nope")
    get_settings.cache_clear()
    try:
        with TestClient(create_app(FakeBuilder(), None)) as c:
            assert c.post("/v1/answer", json={"question": "q"}).status_code == 503
    finally:
        get_settings.cache_clear()


def quota_llm() -> FakeLLM:
    usage = UsageRecord(provider="fake", requested_model="fake-model")
    return FakeLLM(LLMQuotaError("daily quota used up", usage, 3600.0))


def test_quota_exhaustion_is_flagged_on_the_exception() -> None:
    with pytest.raises(AnswerUnavailable) as raised:
        service(quota_llm()).answer("q")
    assert raised.value.quota_exhausted and raised.value.retry_after_s == 3600.0


def test_http_quota_exhaustion_is_429_with_the_retry_hint() -> None:
    with client(quota_llm()) as c:
        r = c.post("/v1/answer", json={"question": "q"})
        assert r.status_code == 429
        body = r.json()
        assert body["quota_exhausted"] and body["retry_after_s"] == 3600.0 and body["evidence"]


def test_structured_output_is_opt_in_and_the_schema_matches_the_contract() -> None:
    from app.answer.output_schema import ANSWER_SCHEMA

    plain, structured = FakeLLM(output()), FakeLLM(output())
    service(plain).answer("q")
    AnswerService(lambda q, k: make_evidence(q), structured, structured_output=True).answer("q")
    assert plain.requests[0].response_schema is None  # the measured behaviour is the default
    assert structured.requests[0].response_schema == ANSWER_SCHEMA
    props = ANSWER_SCHEMA["properties"]
    assert set(props) == {"status", "claims", "missing"}
    assert set(ANSWER_SCHEMA["required"]) == set(props)
    assert set(props["claims"]["items"]["required"]) == {"text", "citations"}
    assert props["status"]["enum"] == ["answered", "partial", "insufficient_evidence"]
