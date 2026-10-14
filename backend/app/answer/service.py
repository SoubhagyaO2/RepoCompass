"""The answer pipeline: evidence -> context -> one model call -> validation -> answer.

The service receives evidence from a callable (`EvidenceSource`) and a model from an `LLMClient`.
It has no other way to learn anything about the repository (plan rule R1).
"""

import hashlib
import time
from collections.abc import Callable

from app.answer.context import (
    CONTEXT_BUDGET_CHARS,
    CONTEXT_BUDGET_CHARS_V2,
    Context,
    build_context,
    build_context_v2,
)
from app.answer.output_schema import ANSWER_SCHEMA
from app.answer.premise import validate_output_v3
from app.answer.prompt import PROMPT_SHA256, SYSTEM_PROMPT
from app.answer.schema import AnswerResponse, Claim, ResolvedCitation, ValidationReport
from app.answer.validate import ValidationResult, validate_output
from app.evidence.schema import EvidenceResponse, EvidenceResponseV2
from app.llm.base import LLMClient, LLMError, LLMQuotaError, LLMRequest, UsageRecord

TOP_K = 8
MAX_OUTPUT_TOKENS = 8192

EvidenceSource = Callable[[str, int], EvidenceResponse]


class AnswerUnavailable(RuntimeError):
    """The model could not be reached. The evidence is kept so the failure is auditable."""

    def __init__(
        self,
        message: str,
        evidence: EvidenceResponse,
        usage: UsageRecord | None,
        quota_exhausted: bool = False,
        retry_after_s: float | None = None,
    ) -> None:
        super().__init__(message)
        self.evidence = evidence
        self.usage = usage
        self.quota_exhausted = quota_exhausted
        self.retry_after_s = retry_after_s


def _resolve(context: Context, result: ValidationResult) -> list[Claim]:
    by_id = context.by_id()
    claims = []
    for claim in result.claims:
        citations = []
        for evidence_id in claim.citations:
            item = by_id[evidence_id].item
            citations.append(
                ResolvedCitation(
                    evidence_id=evidence_id,
                    file=item.file,
                    file_sha256=item.file_sha256,
                    line_start=item.line_start,
                    line_end=item.line_end,
                    symbol=item.symbol.qualified_name if item.symbol else None,
                )
            )
        claims.append(Claim(text=claim.text, citations=citations))
    return claims


def derive_text(claims: list[Claim]) -> str:
    return " ".join(
        f"{c.text} " + "".join(f"[{x.evidence_id}]" for x in c.citations) for c in claims
    )


class AnswerService:
    def __init__(
        self,
        evidence_source: EvidenceSource | None,
        llm: LLMClient,
        top_k: int = TOP_K,
        budget_chars: int = CONTEXT_BUDGET_CHARS,
        budget_chars_v2: int = CONTEXT_BUDGET_CHARS_V2,
        max_output_tokens: int = MAX_OUTPUT_TOKENS,
        system_prompt: str = SYSTEM_PROMPT,
        contract: str = "v1",
        structured_output: bool = False,
    ) -> None:
        if contract not in ("v1", "v3"):
            raise ValueError(f"unknown contract {contract!r}")
        self._source = evidence_source
        self._llm = llm
        self.top_k = top_k
        self._budget = budget_chars
        self._budget_v2 = budget_chars_v2
        self._max_output_tokens = max_output_tokens
        self._prompt = system_prompt
        self._contract = contract
        self._schema = ANSWER_SCHEMA if structured_output and contract == "v1" else None
        self.prompt_sha256 = (
            PROMPT_SHA256
            if system_prompt == SYSTEM_PROMPT
            else hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()
        )

    def answer(self, question: str, top_k: int | None = None) -> AnswerResponse:
        if self._source is None:
            raise RuntimeError("this service has no evidence source")
        evidence = self._source(question, top_k or self.top_k)
        return self.answer_from_evidence(question, evidence)

    def answer_from_evidence(self, question: str, evidence: EvidenceResponse) -> AnswerResponse:
        started = time.perf_counter()
        if isinstance(evidence, EvidenceResponseV2):
            context = build_context_v2(question, evidence, self._budget_v2)
        else:
            context = build_context(question, evidence, self._budget)
        request = LLMRequest(
            system=self._prompt,
            user=context.text,
            max_output_tokens=self._max_output_tokens,
            temperature=0.0,
            response_schema=self._schema,
        )
        try:
            reply = self._llm.generate(request)
        except LLMQuotaError as error:
            raise AnswerUnavailable(
                str(error), evidence, error.usage, True, error.retry_after_s
            ) from error
        except LLMError as error:
            raise AnswerUnavailable(str(error), evidence, error.usage) from error

        assessment = None
        if self._contract == "v3":
            result, assessment = validate_output_v3(reply.text, context)
        else:
            result = validate_output(reply.text, context)
        report = ValidationReport(
            valid=result.valid,
            reasons=result.reasons,
            paths_only_in_uncited_items=result.paths_only_in_uncited_items,
        )
        if result.valid:
            claims = _resolve(context, result)
            status, missing, text = result.status, result.missing, derive_text(claims)
        else:
            claims, status, missing, text = [], "invalid_output", "", None
        return AnswerResponse(
            question=question,
            status=status,  # type: ignore[arg-type]
            claims=claims,
            missing=missing,
            answer_text=text,
            validation=report,
            items_shown=len(context.items),
            items_omitted=context.omitted,
            prompt_sha256=self.prompt_sha256,
            raw_output=reply.text,
            model=reply.usage,
            evidence=evidence,
            timing_ms=round((time.perf_counter() - started) * 1000, 1),
            premise=assessment.premise if assessment else None,
            premise_supported=assessment.premise_supported if assessment else None,
            evidence_relation=assessment.evidence_relation if assessment else None,
        )
