"""The provider boundary of the answer layer (Milestone 13).

An `LLMClient` turns one system prompt and one user message into text plus a usage record. It knows
nothing about retrieval, the index, the graph or evidence: the answer layer hands it strings.
There are no tools, no files and no search on this interface.
"""

from typing import Any, Protocol

from pydantic import BaseModel


class LLMError(RuntimeError):
    """The provider failed (after the allowed retries). Never contains credentials."""

    def __init__(self, message: str, usage: "UsageRecord | None" = None) -> None:
        super().__init__(message)
        self.usage = usage


class LLMQuotaError(LLMError):
    """The provider's quota is used up (a daily cap, or a wait too long to be worth retrying).
    Not retried: every retry would count against the same quota."""

    def __init__(
        self, message: str, usage: "UsageRecord | None" = None, retry_after_s: float | None = None
    ) -> None:
        super().__init__(message, usage)
        self.retry_after_s = retry_after_s


class LLMConfigError(LLMError):
    """The provider is not configured (unknown provider, missing key)."""


class CallError(BaseModel):
    """One failed attempt, recorded in the usage record."""

    status: int | None  # HTTP status, None for transport errors
    message: str


class UsageRecord(BaseModel):
    provider: str
    requested_model: str
    returned_model_version: str | None = None
    reasoning_setting: str = "provider default"
    input_tokens: int | None = None
    output_tokens: int | None = None  # tokens of the visible reply
    thinking_tokens: int | None = None  # reasoning tokens, billed/limited as output
    total_tokens: int | None = None
    finish_reason: str | None = None
    latency_ms: float = 0.0  # wall time of the whole call, retries and waits included
    attempts: int = 0
    retries: int = 0
    errors: list[CallError] = []
    estimated_cost_usd: float = 0.0
    cost_basis: str = "free tier: no charge"


class LLMRequest(BaseModel):
    system: str
    user: str
    max_output_tokens: int = 8192
    temperature: float = 0.0
    json_output: bool = True
    response_schema: dict[str, Any] | None = None  # provider-enforced output shape


class LLMResponse(BaseModel):
    text: str
    usage: UsageRecord


class LLMClient(Protocol):
    provider: str
    model: str

    def generate(self, request: LLMRequest) -> LLMResponse: ...
