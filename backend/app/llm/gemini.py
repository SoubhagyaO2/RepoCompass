"""Gemini provider (REST, standard library only). Retry policy of the M13 plan, section 2:
transport errors, HTTP 429 and 5xx are retried up to 3 times (waits 4, 8, 16 s, or the server's
retry delay when it is at most 60 s); an exhausted quota stops at once; other errors and invalid
model output are never retried. Requests are spaced at least `min_interval_s` apart. Every attempt
and error is recorded; the key is sent in a header and appears in no message, log or record."""

import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from app.llm.base import (
    CallError,
    LLMConfigError,
    LLMError,
    LLMQuotaError,
    LLMRequest,
    LLMResponse,
    UsageRecord,
)

MODEL_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}"
ENDPOINT = MODEL_URL + ":generateContent"
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
RETRY_WAITS_S = (4.0, 8.0, 16.0)
DEFAULT_TIMEOUT_S = 120.0
MIN_INTERVAL_S = 4.0

# (status, body) for a POST, or a GET when the body is empty; status 0 means a transport
# failure with the reason as body text.
Transport = Callable[[str, dict[str, str], bytes, float], tuple[int, bytes]]


def urllib_transport(
    url: str, headers: dict[str, str], body: bytes, timeout: float
) -> tuple[int, bytes]:
    method = "POST" if body else "GET"
    request = urllib.request.Request(url, data=body or None, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return 0, str(error).encode("utf-8", errors="replace")


def _short(message: str, key: str) -> str:
    return message.replace(key, "***")[:300]


def _retry_delay(body: bytes) -> float | None:
    """The server-suggested delay ("retryDelay": "12s") in an error body, if present."""
    match = re.search(rb'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"', body)
    return float(match.group(1)) if match else None


MAX_RETRY_DELAY_S = 60.0


def _quota_exhausted(body: bytes) -> bool:
    """A 429 that waiting a few seconds will not cure: a per-day quota, or a server-suggested
    delay longer than MAX_RETRY_DELAY_S (it once asked for 3 h 52 m)."""
    delay = _retry_delay(body)
    return b"PerDay" in body or (delay is not None and delay > MAX_RETRY_DELAY_S)


class GeminiClient:
    provider = "gemini"

    def __init__(
        self,
        api_key: str,
        model: str,
        transport: Transport = urllib_transport,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        min_interval_s: float = MIN_INTERVAL_S,
        input_price_per_mtok: float | None = None,
        output_price_per_mtok: float | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("an API key is required")
        self.model = model
        self._key = api_key
        self._transport = transport
        self._sleep = sleep
        self._clock = clock
        self._timeout = timeout_s
        self._min_interval = min_interval_s
        self._last_request: float | None = None
        self._prices = (input_price_per_mtok, output_price_per_mtok)

    def __repr__(self) -> str:  # never show the key
        return f"GeminiClient(model={self.model!r})"

    def preflight(self) -> str:
        """One metadata request (a GET, never retried) that proves the key works before any
        generation call. Returns the model resource name; raises without the key in the message."""
        headers = {"x-goog-api-key": self._key}
        url = MODEL_URL.format(model=self.model)
        status, raw = self._transport(url, headers, b"", self._timeout)
        if status == 429:
            raise LLMQuotaError("Gemini preflight: quota exhausted (HTTP 429)")
        if status != 200:
            reason = f"HTTP {status}" if status else "transport error"
            raise LLMConfigError(f"Gemini preflight failed ({reason}); check GEMINI_API_KEY")
        try:
            return str(json.loads(raw).get("name", ""))
        except (json.JSONDecodeError, AttributeError):
            return ""

    # --- request ---------------------------------------------------------------------------

    def _payload(self, request: LLMRequest) -> bytes:
        config: dict[str, Any] = {
            "temperature": request.temperature,
            "maxOutputTokens": request.max_output_tokens,
        }
        if request.json_output:
            config["responseMimeType"] = "application/json"
        if request.response_schema:
            config["responseSchema"] = request.response_schema
        payload = {
            "systemInstruction": {"parts": [{"text": request.system}]},
            "contents": [{"role": "user", "parts": [{"text": request.user}]}],
            "generationConfig": config,
        }
        return json.dumps(payload).encode("utf-8")

    def _space_requests(self) -> None:
        if self._last_request is not None:
            wait = self._min_interval - (self._clock() - self._last_request)
            if wait > 0:
                self._sleep(wait)

    def generate(self, request: LLMRequest) -> LLMResponse:
        url = ENDPOINT.format(model=self.model)
        headers = {"Content-Type": "application/json", "x-goog-api-key": self._key}
        body = self._payload(request)
        usage = UsageRecord(provider=self.provider, requested_model=self.model)
        started = self._clock()

        for attempt in range(len(RETRY_WAITS_S) + 1):
            self._space_requests()
            self._last_request = self._clock()
            status, raw = self._transport(url, headers, body, self._timeout)
            usage.attempts += 1
            if status == 200:
                usage.latency_ms = (self._clock() - started) * 1000
                return self._parse(raw, usage)
            error = CallError(
                status=status or None, message=_short(raw.decode("utf-8", "replace"), self._key)
            )
            usage.errors.append(error)
            if status == 429 and _quota_exhausted(raw):
                # Retrying would not help and every try counts against the quota (plan deviation 3)
                usage.latency_ms = (self._clock() - started) * 1000
                raise LLMQuotaError(
                    f"Gemini quota exhausted: {error.message}", usage, _retry_delay(raw)
                )
            retryable = status == 0 or status in RETRYABLE_STATUS
            if not retryable or attempt == len(RETRY_WAITS_S):
                usage.latency_ms = (self._clock() - started) * 1000
                reason = f"HTTP {status}" if status else "transport error"
                raise LLMError(f"Gemini request failed ({reason}): {error.message}", usage)
            usage.retries += 1
            self._sleep(max(RETRY_WAITS_S[attempt], _retry_delay(raw) or 0.0))
        raise AssertionError("unreachable")  # pragma: no cover

    # --- response --------------------------------------------------------------------------

    def _parse(self, raw: bytes, usage: UsageRecord) -> LLMResponse:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as error:
            raise LLMError("Gemini returned a body that is not JSON", usage) from error
        usage.returned_model_version = data.get("modelVersion")
        meta = data.get("usageMetadata") or {}
        usage.input_tokens = meta.get("promptTokenCount")
        usage.output_tokens = meta.get("candidatesTokenCount")
        usage.thinking_tokens = meta.get("thoughtsTokenCount")
        usage.total_tokens = meta.get("totalTokenCount")
        usage.estimated_cost_usd, usage.cost_basis = self._cost(usage)

        candidates = data.get("candidates") or []
        if not candidates:
            block = (data.get("promptFeedback") or {}).get("blockReason", "no candidates")
            usage.finish_reason = f"BLOCKED:{block}"
            return LLMResponse(text="", usage=usage)
        candidate = candidates[0]
        usage.finish_reason = candidate.get("finishReason")
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        return LLMResponse(text=text, usage=usage)

    def _cost(self, usage: UsageRecord) -> tuple[float, str]:
        price_in, price_out = self._prices
        if price_in is None or price_out is None:
            return 0.0, "free tier: no charge"
        billed_out = (usage.output_tokens or 0) + (usage.thinking_tokens or 0)
        cost = ((usage.input_tokens or 0) * price_in + billed_out * price_out) / 1_000_000
        return cost, "estimate from the supplied price table (USD per million tokens)"
