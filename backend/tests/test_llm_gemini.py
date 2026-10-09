"""The Gemini client, with a fake transport: no network (plan section 6, tests 5-7)."""

import json
from typing import Any

import pytest

from app.config import Settings
from app.llm import get_llm_client
from app.llm.base import LLMConfigError, LLMError, LLMQuotaError, LLMRequest
from app.llm.gemini import GeminiClient

KEY = "FAKE-KEY-0123456789"
REQUEST = LLMRequest(system="sys", user="usr")


def ok_body(text: str = '{"status": "ok"}', model: str = "gemini-3.8-flash") -> bytes:
    return json.dumps(
        {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": "thinking...", "thought": True}, {"text": text}]
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 120,
                "candidatesTokenCount": 9,
                "thoughtsTokenCount": 83,
                "totalTokenCount": 212,
            },
            "modelVersion": model,
        }
    ).encode()


class Script:
    """A scripted transport + clock: records requests and sleeps without waiting."""

    def __init__(self, *replies: tuple[int, bytes]) -> None:
        self.replies = list(replies)
        self.calls: list[tuple[str, dict[str, str], dict[str, Any]]] = []
        self.sleeps: list[float] = []
        self.now = 0.0

    def transport(self, url: str, headers: dict[str, str], body: bytes, timeout: float):
        self.calls.append((url, headers, json.loads(body)))
        return self.replies.pop(0)

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def clock(self) -> float:
        return self.now

    def client(self, **kwargs: Any) -> GeminiClient:
        return GeminiClient(
            KEY, "gemini-3.7-flash", self.transport, self.sleep, self.clock, **kwargs
        )


def test_request_shape_and_key_in_header_only() -> None:
    script = Script((200, ok_body()))
    script.client().generate(LLMRequest(system="S", user="U", max_output_tokens=777))
    url, headers, body = script.calls[0]
    assert url.endswith("/models/gemini-3.7-flash:generateContent")
    assert KEY not in url and headers["x-goog-api-key"] == KEY
    assert body["systemInstruction"]["parts"][0]["text"] == "S"
    assert body["contents"] == [{"role": "user", "parts": [{"text": "U"}]}]
    assert body["generationConfig"] == {
        "temperature": 0.0,
        "maxOutputTokens": 777,
        "responseMimeType": "application/json",
    }
    assert "tools" not in body and "toolConfig" not in body  # rule R2


def test_usage_record_has_everything_the_plan_requires() -> None:
    script = Script((200, ok_body()))
    response = script.client().generate(REQUEST)
    u = response.usage
    assert response.text == '{"status": "ok"}'  # thought parts are not part of the reply
    assert (u.provider, u.requested_model) == ("gemini", "gemini-3.7-flash")
    assert u.returned_model_version == "gemini-3.8-flash"
    assert (u.input_tokens, u.output_tokens, u.thinking_tokens, u.total_tokens) == (120, 9, 83, 212)
    assert u.finish_reason == "STOP" and u.attempts == 1 and u.retries == 0 and u.errors == []
    assert u.reasoning_setting == "provider default"
    assert u.estimated_cost_usd == 0.0 and "free" in u.cost_basis


def test_paid_tier_estimate_only_with_a_price_table() -> None:
    script = Script((200, ok_body()))
    usage = (
        script.client(input_price_per_mtok=0.5, output_price_per_mtok=2.0).generate(REQUEST).usage
    )
    assert usage.estimated_cost_usd == pytest.approx((120 * 0.5 + (9 + 83) * 2.0) / 1e6)
    assert "price table" in usage.cost_basis


@pytest.mark.parametrize("status", [429, 500, 503])
def test_transient_errors_are_retried_and_recorded(status: int) -> None:
    script = Script((status, b'{"error": {"message": "busy"}}'), (200, ok_body()))
    response = script.client().generate(REQUEST)
    assert response.text and response.usage.attempts == 2 and response.usage.retries == 1
    assert [e.status for e in response.usage.errors] == [status]
    assert script.sleeps[0] == 4.0


def test_transport_errors_are_retried_with_growing_waits() -> None:
    script = Script((0, b"connection reset"), (503, b"x"), (503, b"x"), (200, ok_body()))
    response = script.client(min_interval_s=0).generate(REQUEST)
    assert response.usage.attempts == 4 and response.usage.retries == 3
    assert script.sleeps == [4.0, 8.0, 16.0]
    assert [e.status for e in response.usage.errors] == [None, 503, 503]


def test_gives_up_after_three_retries_and_keeps_the_usage() -> None:
    script = Script(*[(503, b"busy")] * 4)
    with pytest.raises(LLMError) as raised:
        script.client(min_interval_s=0).generate(REQUEST)
    assert "HTTP 503" in str(raised.value)
    assert raised.value.usage is not None and raised.value.usage.attempts == 4


def test_the_server_retry_delay_is_honoured_when_longer() -> None:
    body = b'{"error": {"details": [{"retryDelay": "30s"}]}}'
    script = Script((429, body), (200, ok_body()))
    script.client(min_interval_s=0).generate(REQUEST)
    assert script.sleeps == [30.0]


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_other_errors_are_not_retried(status: int) -> None:
    script = Script((status, b'{"error": {"message": "no"}}'))
    with pytest.raises(LLMError):
        script.client().generate(REQUEST)
    assert len(script.calls) == 1


def test_requests_are_spaced_at_least_the_minimum_interval() -> None:
    script = Script((200, ok_body()), (200, ok_body()))
    client = script.client()
    client.generate(REQUEST)
    script.now += 1.5  # 1.5 s passes between the two calls
    client.generate(REQUEST)
    assert script.sleeps == [pytest.approx(2.5)]


def test_a_blocked_prompt_yields_empty_text_not_an_exception() -> None:
    body = json.dumps({"promptFeedback": {"blockReason": "SAFETY"}, "modelVersion": "m"}).encode()
    response = Script((200, body)).client().generate(REQUEST)
    assert response.text == "" and response.usage.finish_reason == "BLOCKED:SAFETY"


def test_non_json_success_body_is_an_error() -> None:
    with pytest.raises(LLMError):
        Script((200, b"<html>")).client().generate(REQUEST)


def test_the_key_never_appears_in_errors_records_or_repr() -> None:
    echo = f'{{"error": {{"message": "bad key {KEY}"}}}}'.encode()
    script = Script((401, echo))
    client = script.client()
    with pytest.raises(LLMError) as raised:
        client.generate(REQUEST)
    dumped = str(raised.value) + repr(client) + raised.value.usage.model_dump_json()  # type: ignore[union-attr]
    assert KEY not in dumped


def test_model_version_changes_are_visible_per_call() -> None:
    script = Script((200, ok_body(model="a")), (200, ok_body(model="b")))
    client = script.client()
    versions = [client.generate(REQUEST).usage.returned_model_version for _ in range(2)]
    assert versions == ["a", "b"]


# --- provider configuration ----------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_ambient_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's own GEMINI_API_KEY (environment variables beat .env) must not leak in."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("REPOCOMPASS_LLM_PROVIDER", raising=False)


def settings(**values: Any) -> Settings:
    return Settings(_env_file=None, **values)


def test_provider_selection() -> None:
    client = get_llm_client(settings(llm_provider="gemini", GEMINI_API_KEY=KEY))
    assert client.provider == "gemini" and client.model == "gemini-3.7-flash"


def test_unknown_provider_and_missing_key_are_clear_errors() -> None:
    with pytest.raises(LLMConfigError, match="unknown LLM provider 'nope'"):
        get_llm_client(settings(llm_provider="nope", GEMINI_API_KEY=KEY))
    with pytest.raises(LLMConfigError, match="GEMINI_API_KEY is not set"):
        get_llm_client(settings(llm_provider="gemini"))


def test_settings_never_reveal_the_key() -> None:
    s = settings(GEMINI_API_KEY=KEY)
    assert KEY not in repr(s) and KEY not in str(s) and KEY not in s.model_dump_json()


# --- quota handling (plan deviation 3) ----------------------------------------------------------

DAILY = (
    b'{"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "Quota exceeded: '
    b'generate_content_free_tier_requests, limit: 20", "details": [{"@type": "type.googleapis.com/'
    b'google.rpc.QuotaFailure", "violations": [{"quotaId": '
    b'"GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}, {"@type": "type.googleapis.com/'
    b'google.rpc.RetryInfo", "retryDelay": "13933s"}]}}'
)


def test_a_daily_quota_stops_at_once_without_retrying_or_sleeping() -> None:
    script = Script((429, DAILY), (200, ok_body()))
    with pytest.raises(LLMQuotaError) as raised:
        script.client().generate(REQUEST)
    assert len(script.calls) == 1 and script.sleeps == []
    assert raised.value.retry_after_s == 13933.0
    assert raised.value.usage is not None and raised.value.usage.attempts == 1


def test_a_very_long_retry_delay_is_a_quota_stop_even_without_the_daily_marker() -> None:
    body = b'{"error": {"details": [{"retryDelay": "600s"}]}}'
    with pytest.raises(LLMQuotaError):
        Script((429, body)).client().generate(REQUEST)


def test_a_short_rate_limit_is_still_retried() -> None:
    body = b'{"error": {"details": [{"retryDelay": "20s"}]}}'
    script = Script((429, body), (200, ok_body()))
    assert script.client(min_interval_s=0).generate(REQUEST).usage.retries == 1
    assert script.sleeps == [20.0]
