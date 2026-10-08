"""Opening the persisted live index for the API and the command line."""

from pathlib import Path

from app.config import Settings
from app.evidence.builder import EvidenceBuilder
from app.indexing.live_index import IndexStateError, LiveIndex
from app.retrieval.embedder import Embedder, get_embedder
from app.retrieval.vector_store import VectorStore


def open_live_index(
    settings: Settings,
    repository: str,
    root: Path,
    embedder: Embedder | None = None,
) -> tuple[LiveIndex, VectorStore]:
    """The live index of `repository` under `settings.data_dir`. The caller closes the store."""
    store = VectorStore(settings.live_qdrant_path)
    index = LiveIndex(
        repository,
        root,
        settings.live_state_dir(repository),
        store,
        embedder or get_embedder(settings),
        settings.baseline_chunk_size,
        settings.baseline_chunk_overlap,
    )
    return index, store


def load_builder(
    settings: Settings, repository: str, root: Path, revision: str | None = None
) -> tuple[EvidenceBuilder, VectorStore]:
    """Load the stored index (never builds one). IndexStateError if it is missing or unusable."""
    index, store = open_live_index(settings, repository, root)
    try:
        if not index.load():
            raise IndexStateError(
                f"no live index for {repository!r} under {settings.live_state_dir(repository)}; "
                "build one first: python -m app.evidence --build ..."
            )
    except Exception:
        store.close()
        raise
    return EvidenceBuilder(index, revision), store
