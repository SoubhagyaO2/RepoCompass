"""Answer-layer data models. An answer is a list of claims, each with resolved citations."""

from typing import Literal

from pydantic import BaseModel, SerializeAsAny

from app.evidence.schema import EvidenceResponse
from app.llm.base import UsageRecord

ModelStatus = Literal["answered", "partial", "insufficient_evidence"]
AnswerStatus = Literal["answered", "partial", "insufficient_evidence", "invalid_output"]


class ResolvedCitation(BaseModel):
    """A model-written evidence id resolved through the evidence response, never through the
    model's own words."""

    evidence_id: str
    file: str
    file_sha256: str
    line_start: int
    line_end: int
    symbol: str | None


class Claim(BaseModel):
    text: str
    citations: list[ResolvedCitation]


class ValidationReport(BaseModel):
    """Deterministic citation validity. It does not say the cited evidence supports the claim:
    that is judged by people (plan section 4c)."""

    valid: bool
    reasons: list[str]
    paths_only_in_uncited_items: int = 0  # accepted, but reported


class AnswerResponse(BaseModel):
    question: str
    status: AnswerStatus
    claims: list[Claim]
    missing: str
    answer_text: str | None  # derived from the claims; None when the output was invalid
    validation: ValidationReport
    items_shown: int
    items_omitted: int
    prompt_sha256: str
    raw_output: str
    model: UsageRecord
    evidence: SerializeAsAny[EvidenceResponse]
    timing_ms: float | None = None
    # contract v3 only (Milestone 15); None under the baseline contract or for invalid output
    premise: str | None = None
    premise_supported: bool | None = None
    evidence_relation: str | None = None
