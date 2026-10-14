"""FastAPI application entry point.

Run with:  uvicorn app.main:app --reload   (from the backend/ directory)

To serve evidence, set REPOCOMPASS_LIVE_REPOSITORY and REPOCOMPASS_LIVE_ROOT to a repository whose
live index was built with `python -m app.evidence --build`; without them only /health is useful.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.answer.service import AnswerService
from app.api.answer import router as answer_router
from app.api.evidence import router as evidence_router
from app.api.evidence import router_v2 as evidence_v2_router
from app.api.health import router as health_router
from app.config import get_settings
from app.evidence.builder import EvidenceBuilder
from app.evidence.service import load_builder
from app.indexing.live_index import IndexStateError
from app.llm import get_llm_client
from app.llm.base import LLMClient, LLMConfigError


def create_app(builder: EvidenceBuilder | None = None, llm: LLMClient | None = None) -> FastAPI:
    """`builder` and `llm` let tests (and embedders) supply an index and a model; otherwise the
    index is loaded at startup when the live repository is configured, and the answer layer is
    enabled when the LLM provider is configured (without it /v1/answer returns 503)."""
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        store = None
        app.state.evidence_builder = builder
        if builder is None and settings.live_repository and settings.live_root:
            try:
                app.state.evidence_builder, store = load_builder(
                    settings, settings.live_repository, settings.live_root
                )
            except IndexStateError as error:
                raise RuntimeError(f"cannot load the live index: {error}") from None
        app.state.answer_service = None
        active = app.state.evidence_builder
        if active is not None:
            try:
                client = llm or get_llm_client(settings)
            except LLMConfigError:
                client = None
            if client is not None:
                app.state.answer_service = AnswerService(
                    lambda q, k: active.build(q, k, include_text=True),
                    client,
                    structured_output=settings.structured_output,
                )
        try:
            yield
        finally:
            if store is not None:
                store.close()

    app = FastAPI(title=settings.app_name, version=__version__, lifespan=lifespan)
    app.include_router(health_router)
    app.include_router(evidence_router)
    app.include_router(evidence_v2_router)
    app.include_router(answer_router)
    return app


app = create_app()
