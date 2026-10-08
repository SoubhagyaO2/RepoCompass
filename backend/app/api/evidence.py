"""HTTP interface of the Evidence API (Milestone 12). No prose is generated here."""

import time
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request

from app.evidence.builder import (
    DEFAULT_TOP_K,
    DEPTH,
    MAX_QUERY_CHARS,
    EvidenceBuilder,
    EvidenceQueryError,
)
from app.evidence.schema import EvidenceResponse, IndexInfo

router = APIRouter(prefix="/v1", tags=["evidence"])


def get_builder(request: Request) -> EvidenceBuilder:
    builder: EvidenceBuilder | None = getattr(request.app.state, "evidence_builder", None)
    if builder is None:
        raise HTTPException(503, "no index is loaded (set REPOCOMPASS_LIVE_REPOSITORY)")
    return builder


@router.get("/evidence", response_model=EvidenceResponse)
def evidence(
    request: Request,
    q: Annotated[str, Query(min_length=1, max_length=MAX_QUERY_CHARS)],
    top_k: Annotated[int, Query(ge=1, le=DEPTH)] = DEFAULT_TOP_K,
    include_text: bool = False,
) -> EvidenceResponse:
    """Hybrid retrieval as evidence: one item per file, each citable by file and line range."""
    builder = get_builder(request)
    started = time.perf_counter()
    try:
        response = builder.build(q, top_k, include_text)
    except EvidenceQueryError as error:
        raise HTTPException(422, str(error)) from error
    response.timing_ms = round((time.perf_counter() - started) * 1000, 2)
    return response


@router.get("/index/status", response_model=IndexInfo)
def index_status(request: Request) -> IndexInfo:
    return get_builder(request).index_info()
