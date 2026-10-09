"""HTTP interface of the grounded answer layer (Milestone 13)."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.answer.schema import AnswerResponse
from app.answer.service import AnswerService, AnswerUnavailable
from app.evidence.builder import DEPTH, MAX_QUERY_CHARS

router = APIRouter(prefix="/v1", tags=["answer"])


class AnswerRequest(BaseModel):
    question: Annotated[str, Field(min_length=1, max_length=MAX_QUERY_CHARS)]
    top_k: Annotated[int | None, Field(ge=1, le=DEPTH)] = None


def get_service(request: Request) -> AnswerService:
    service: AnswerService | None = getattr(request.app.state, "answer_service", None)
    if service is None:
        raise HTTPException(503, "the answer layer is not configured (index and LLM key needed)")
    return service


@router.post("/answer", response_model=AnswerResponse)
def answer(body: AnswerRequest, request: Request) -> AnswerResponse | JSONResponse:
    """A grounded answer: claims with resolved citations, or an abstention, with the evidence."""
    if not body.question.strip():
        raise HTTPException(422, "the question is empty")
    service = get_service(request)
    try:
        return service.answer(body.question, body.top_k)
    except AnswerUnavailable as error:
        return JSONResponse(
            status_code=429 if error.quota_exhausted else 502,
            content={
                "quota_exhausted": error.quota_exhausted,
                "retry_after_s": error.retry_after_s,
                "error": str(error),
                "usage": error.usage.model_dump(mode="json") if error.usage else None,
                "evidence": error.evidence.model_dump(mode="json"),
            },
        )
