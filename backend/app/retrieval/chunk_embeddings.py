"""Embedding retrieval over AST-aware chunks (the control for the AST hybrid).

Same embedding model, same naive input (raw chunk content only) and same vector store as the
fixed-chunk baseline; only the chunk boundaries differ.
"""

import re
import time
from typing import Any

from pydantic import BaseModel

from app.indexing.service import build_index
from app.models.repository import IngestedRepository
from app.models.search import SearchHit
from app.retrieval.embedder import Embedder
from app.retrieval.vector_store import VectorStore


class AstEmbeddingStats(BaseModel):
    files: int
    chunks: int
    chunking_seconds: float
    embedding_seconds: float
    upsert_seconds: float
    total_seconds: float


def ast_collection_name(repository: str) -> str:
    """Qdrant collection for the AST-chunk embedding index of "owner/name"."""
    return "ast__" + re.sub(r"[^A-Za-z0-9_-]", "_", repository.replace("/", "__"))


class ChunkEmbeddingRetriever:
    def __init__(self, embedder: Embedder, store: VectorStore, collection: str) -> None:
        self._embedder = embedder
        self._store = store
        self._collection = collection

    def search(self, query: str, k: int) -> list[SearchHit]:
        results = self._store.search(self._collection, self._embedder.embed_query(query), k)
        return [SearchHit.model_validate({**payload, "score": score}) for score, payload in results]


def build_ast_embeddings(
    ingested: IngestedRepository,
    embedder: Embedder,
    store: VectorStore,
    show_progress: bool = False,
) -> tuple[ChunkEmbeddingRetriever, AstEmbeddingStats]:
    start = time.perf_counter()
    chunks, index_stats = build_index(ingested)  # AST-aware chunks of every Python file
    t1 = time.perf_counter()

    # Naive on purpose, exactly as for the fixed-chunk baseline: embed the raw content only.
    vectors = embedder.embed_documents([c.content for c in chunks], show_progress) if chunks else []
    t2 = time.perf_counter()

    payloads: list[dict[str, Any]] = [
        SearchHit(
            file_path=c.file_path,
            start_line=c.start_line,
            end_line=c.end_line,
            score=0.0,
            content=c.content,
            category=c.category,
            symbol_name=c.qualified_name,
            symbol_type=c.symbol_type,
        ).model_dump()
        for c in chunks
    ]
    name = ast_collection_name(ingested.ref.full_name)
    store.recreate_collection(name, embedder.dimension)
    store.upsert(name, vectors, payloads)
    t3 = time.perf_counter()

    stats = AstEmbeddingStats(
        files=index_stats.files_indexed,
        chunks=len(chunks),
        chunking_seconds=t1 - start,
        embedding_seconds=t2 - t1,
        upsert_seconds=t3 - t2,
        total_seconds=t3 - start,
    )
    return ChunkEmbeddingRetriever(embedder, store, name), stats
