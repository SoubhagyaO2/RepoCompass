from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app import __version__

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    version: str
    index_loaded: bool = False
    answer_enabled: bool = False


@router.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    """The API has no page of its own: send a browser to the interactive docs."""
    return RedirectResponse("/docs")


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    """Liveness check, plus whether an index is loaded and the answer layer is configured."""
    state = request.app.state
    return HealthResponse(
        status="ok",
        version=__version__,
        index_loaded=getattr(state, "evidence_builder", None) is not None,
        answer_enabled=getattr(state, "answer_service", None) is not None,
    )
