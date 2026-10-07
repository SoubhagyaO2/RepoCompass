"""BM25 over chunks: the same retriever for fixed-size chunks and for AST-aware chunks.

Only the chunk *content* is indexed (no file path, no symbol name), for both chunkings, so the
two differ in chunk boundaries and nothing else.
"""

import time

from pydantic import BaseModel

from app.chunking.fixed_chunker import DEFAULT_OVERLAP, DEFAULT_SIZE, chunk_repository
from app.indexing.service import build_index
from app.models.repository import IngestedRepository
from app.models.search import SearchHit
from app.retrieval.bm25 import BM25Index


class Bm25BuildStats(BaseModel):
    files: int
    chunks: int
    chunking_seconds: float
    index_seconds: float
    total_seconds: float


class Bm25ChunkRetriever:
    def __init__(self, hits: list[SearchHit], contents: list[str]) -> None:
        self._hits = hits  # one template per chunk (score filled in at search time)
        self._index = BM25Index(contents)

    def search(self, query: str, k: int) -> list[SearchHit]:
        return [
            self._hits[doc_id].model_copy(update={"score": score})
            for doc_id, score in self._index.search(query, k)
        ]


def build_fixed_bm25(
    ingested: IngestedRepository, size: int = DEFAULT_SIZE, overlap: int = DEFAULT_OVERLAP
) -> tuple[Bm25ChunkRetriever, Bm25BuildStats]:
    start = time.perf_counter()
    chunks, files = chunk_repository(ingested, size, overlap)
    chunked = time.perf_counter()
    hits = [
        SearchHit(
            file_path=c.file_path,
            start_line=c.start_line,
            end_line=c.end_line,
            score=0.0,
            content=c.content,
            category=c.category,
        )
        for c in chunks
    ]
    retriever = Bm25ChunkRetriever(hits, [c.content for c in chunks])
    done = time.perf_counter()
    return retriever, Bm25BuildStats(
        files=files,
        chunks=len(chunks),
        chunking_seconds=chunked - start,
        index_seconds=done - chunked,
        total_seconds=done - start,
    )


def build_ast_bm25(ingested: IngestedRepository) -> tuple[Bm25ChunkRetriever, Bm25BuildStats]:
    start = time.perf_counter()
    chunks, stats = build_index(ingested)  # parse every Python file, AST-aware chunks
    chunked = time.perf_counter()
    hits = [
        SearchHit(
            file_path=c.file_path,
            start_line=c.start_line,
            end_line=c.end_line,
            score=0.0,
            content=c.content,
            category=c.category,
            symbol_name=c.qualified_name,
            symbol_type=c.symbol_type,
        )
        for c in chunks
    ]
    retriever = Bm25ChunkRetriever(hits, [c.content for c in chunks])
    done = time.perf_counter()
    return retriever, Bm25BuildStats(
        files=stats.files_indexed,
        chunks=len(chunks),
        chunking_seconds=chunked - start,
        index_seconds=done - chunked,
        total_seconds=done - start,
    )
