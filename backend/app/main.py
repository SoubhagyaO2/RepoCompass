"""FastAPI application entry point.

Run with:  uvicorn app.main:app --reload   (from the backend/ directory)

Repositories reach the API in two ways: a repository whose live index was built with
`python -m app.evidence --build` and named by REPOCOMPASS_LIVE_REPOSITORY and
REPOCOMPASS_LIVE_ROOT (the default), and repositories connected by URL through
`POST /v1/repositories`, which are reloaded from disk at the next start.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api.answer import router as answer_router
from app.api.evidence import router as evidence_router
from app.api.evidence import router_v2 as evidence_v2_router
from app.api.health import router as health_router
from app.api.repositories import router as repositories_router
from app.config import get_settings
from app.evidence.builder import EvidenceBuilder
from app.evidence.service import load_builder
from app.indexing.live_index import IndexStateError
from app.llm import get_llm_client
from app.llm.base import LLMClient, LLMConfigError
from app.repositories import RepositoryRegistry


def create_app(
    builder: EvidenceBuilder | None = None,
    llm: LLMClient | None = None,
    registry: RepositoryRegistry | None = None,
) -> FastAPI:
    """`builder`, `llm` and `registry` let tests (and embedders) supply an index, a model and the
    set of repositories; otherwise the configured index is loaded at startup, connected
    repositories are reloaded, and the answer layer is enabled when the LLM provider is configured
    (without it /v1/answer returns 503)."""
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        client = llm
        if client is None and registry is None:
            try:
                client = get_llm_client(settings)
            except LLMConfigError:
                client = None
        reg = registry or RepositoryRegistry(settings, llm=client)
        default = builder
        index = getattr(builder, "index", None)  # a test double may have no index
        if registry is None and index is not None:
            reg.adopt(index.store, index.embedder)
        if default is None and registry is None and settings.live_repository and settings.live_root:
            try:
                default, store = load_builder(
                    settings, settings.live_repository, settings.live_root
                )
            except IndexStateError as error:
                raise RuntimeError(f"cannot load the live index: {error}") from None
            reg.adopt(store, default.index.embedder, owns_store=True)
        if default is not None and registry is None:
            name = getattr(getattr(default, "index", None), "repository", "default")
            reg.register_default(name, name, default)
        if registry is None and builder is None:
            reg.load_existing()
        app.state.registry = reg
        app.state.evidence_builder = default
        entry = reg.get(reg.default_id) if reg.default_id else None
        app.state.answer_service = entry.service if entry else None
        try:
            yield
        finally:
            if registry is None:
                reg.shutdown()

    app = FastAPI(title=settings.app_name, version=__version__, lifespan=lifespan)
    app.include_router(health_router)
    app.include_router(evidence_router)
    app.include_router(evidence_v2_router)
    app.include_router(answer_router)
    app.include_router(repositories_router)
    return app


app = create_app()
