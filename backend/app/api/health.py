from fastapi import APIRouter, Request
from pydantic import BaseModel

from app import __version__

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    version: str
    index_loaded: bool = False
    answer_enabled: bool = False


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
