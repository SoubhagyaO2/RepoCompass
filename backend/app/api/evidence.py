"""HTTP interface of the Evidence API (Milestone 12). No prose is generated here."""

import time
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request

from app.evidence.builder import (
    DEFAULT_EXTRA_CHUNKS,
    DEFAULT_TOP_K,
    DEPTH,
    MAX_EXTRA_CHUNKS,
    MAX_QUERY_CHARS,
    EvidenceBuilder,
    EvidenceQueryError,
)
from app.evidence.schema import EvidenceResponse, EvidenceResponseV2, IndexInfo
from app.repositories import RepositoryNotFound, RepositoryNotReady, RepositoryRegistry

router = APIRouter(prefix="/v1", tags=["evidence"])
router_v2 = APIRouter(prefix="/v2", tags=["evidence"])


def get_builder(request: Request, repository: str | None = None) -> EvidenceBuilder:
    registry: RepositoryRegistry | None = getattr(request.app.state, "registry", None)
    if registry is None:
        raise HTTPException(503, "no index is loaded (set REPOCOMPASS_LIVE_REPOSITORY)")
    try:
        return registry.builder(repository)
    except RepositoryNotFound as error:
        if repository:
            raise HTTPException(404, str(error)) from error
        raise HTTPException(503, "no index is loaded (connect a repository first)") from error
    except RepositoryNotReady as error:
        raise HTTPException(409, str(error)) from error


@router.get("/evidence", response_model=EvidenceResponse)
def evidence(
    request: Request,
    q: Annotated[str, Query(min_length=1, max_length=MAX_QUERY_CHARS)],
    top_k: Annotated[int, Query(ge=1, le=DEPTH)] = DEFAULT_TOP_K,
    include_text: bool = False,
    repository: str | None = None,
) -> EvidenceResponse:
    """Hybrid retrieval as evidence: one item per file, each citable by file and line range."""
    builder = get_builder(request, repository)
    started = time.perf_counter()
    try:
        response = builder.build(q, top_k, include_text)
    except EvidenceQueryError as error:
        raise HTTPException(422, str(error)) from error
    response.timing_ms = round((time.perf_counter() - started) * 1000, 2)
    return response


@router.get("/index/status", response_model=IndexInfo)
def index_status(request: Request, repository: str | None = None) -> IndexInfo:
    return get_builder(request, repository).index_info()


@router_v2.get("/evidence", response_model=EvidenceResponseV2)
def evidence_v2(
    request: Request,
    q: Annotated[str, Query(min_length=1, max_length=MAX_QUERY_CHARS)],
    top_k: Annotated[int, Query(ge=1, le=DEPTH)] = DEFAULT_TOP_K,
    include_text: bool = False,
    extra_chunks: Annotated[int, Query(ge=0, le=MAX_EXTRA_CHUNKS)] = DEFAULT_EXTRA_CHUNKS,
    repository: str | None = None,
) -> EvidenceResponseV2:
    """Schema v2: the v1 evidence plus up to `extra_chunks` more chunks of each retrieved file."""
    builder = get_builder(request, repository)
    started = time.perf_counter()
    try:
        response = builder.build_v2(q, top_k, include_text, extra_chunks)
    except EvidenceQueryError as error:
        raise HTTPException(422, str(error)) from error
    response.timing_ms = round((time.perf_counter() - started) * 1000, 2)
    return response
