from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app import __version__
from app.config import get_settings

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    version: str
    index_loaded: bool = False
    answer_enabled: bool = False
    indexing_enabled: bool = False


@router.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    """The API has no page of its own: send a browser to the interactive docs."""
    return RedirectResponse("/docs")


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    """Liveness check, plus whether an index is loaded and the answer layer is configured."""
    registry = getattr(request.app.state, "registry", None)
    entries = registry.list() if registry is not None else []
    return HealthResponse(
        status="ok",
        version=__version__,
        index_loaded=any(e.status == "ready" for e in entries),
        answer_enabled=any(e.status == "ready" and e.service is not None for e in entries),
        indexing_enabled=(
            registry.indexing_enabled
            if registry is not None
            else get_settings().enable_repo_indexing
        ),
    )
