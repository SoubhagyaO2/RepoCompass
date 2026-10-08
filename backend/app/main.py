"""FastAPI application entry point.

Run with:  uvicorn app.main:app --reload   (from the backend/ directory)

To serve evidence, set REPOCOMPASS_LIVE_REPOSITORY and REPOCOMPASS_LIVE_ROOT to a repository whose
live index was built with `python -m app.evidence --build`; without them only /health is useful.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api.evidence import router as evidence_router
from app.api.health import router as health_router
from app.config import get_settings
from app.evidence.builder import EvidenceBuilder
from app.evidence.service import load_builder


def create_app(builder: EvidenceBuilder | None = None) -> FastAPI:
    """`builder` lets tests (and embedders) supply an index; otherwise one is loaded at startup
    when the live repository is configured."""
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        store = None
        app.state.evidence_builder = builder
        if builder is None and settings.live_repository and settings.live_root:
            app.state.evidence_builder, store = load_builder(
                settings, settings.live_repository, settings.live_root
            )
        try:
            yield
        finally:
            if store is not None:
                store.close()

    app = FastAPI(title=settings.app_name, version=__version__, lifespan=lifespan)
    app.include_router(health_router)
    app.include_router(evidence_router)
    return app


app = create_app()
