"""Index a repository: ingest -> parse -> chunk -> save."""

import time
from collections import Counter
from pathlib import Path

from pydantic import BaseModel

from app.chunking.ast_chunker import chunk_python_source
from app.config import Settings, get_settings
from app.ingestion.service import ingest_repository
from app.models.chunk import Chunk
from app.models.repository import IngestedRepository

# Rough size an embedding model (~512 tokens) can read in full. Used only to *report* how
# many chunks are larger; nothing is split or truncated yet.
LARGE_CHUNK_CHARS = 2000


class SizeStats(BaseModel):
    median: int
    p95: int
    max: int


class IndexStats(BaseModel):
    repository: str
    files_indexed: int
    files_with_syntax_errors: int
    chunks_by_type: dict[str, int]
    chunks_by_category: dict[str, int]
    chunk_chars: SizeStats
    large_chunks: int  # chunks over LARGE_CHUNK_CHARS characters
    seconds: float


class IndexResult(BaseModel):
    chunks: list[Chunk]
    stats: IndexStats
    chunks_path: Path | None = None


def build_index(ingested: IngestedRepository) -> tuple[list[Chunk], IndexStats]:
    """Parse and chunk every Python file of an already-ingested repository."""
    start = time.perf_counter()
    repository = ingested.ref.full_name
    chunks: list[Chunk] = []
    files = errors = 0
    for f in ingested.files:
        if f.language != "Python":
            continue
        source = (ingested.local_path / f.path).read_bytes()
        parsed, file_chunks = chunk_python_source(source, f.path, repository)
        files += 1
        errors += parsed.has_errors
        chunks.extend(file_chunks)
    return chunks, compute_stats(repository, chunks, files, errors, time.perf_counter() - start)


def compute_stats(
    repository: str, chunks: list[Chunk], files: int, errors: int, seconds: float
) -> IndexStats:
    sizes = sorted(len(c.content) for c in chunks)
    size_stats = SizeStats(
        median=sizes[len(sizes) // 2] if sizes else 0,
        p95=sizes[min(len(sizes) - 1, int(len(sizes) * 0.95))] if sizes else 0,
        max=sizes[-1] if sizes else 0,
    )
    return IndexStats(
        repository=repository,
        files_indexed=files,
        files_with_syntax_errors=errors,
        chunks_by_type=dict(Counter(c.symbol_type for c in chunks)),
        chunks_by_category=dict(Counter(c.category for c in chunks)),
        chunk_chars=size_stats,
        large_chunks=sum(1 for s in sizes if s > LARGE_CHUNK_CHARS),
        seconds=seconds,
    )


def index_repository(url: str, settings: Settings | None = None) -> IndexResult:
    settings = settings or get_settings()
    ingested = ingest_repository(url, settings)
    chunks, stats = build_index(ingested)
    path = settings.index_dir / ingested.ref.owner / ingested.ref.name / "chunks.jsonl"
    save_chunks(chunks, path)
    return IndexResult(chunks=chunks, stats=stats, chunks_path=path)


def save_chunks(chunks: list[Chunk], path: Path) -> None:
    """Write one JSON object per line (easy to stream, diff and inspect)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for chunk in chunks:
            f.write(chunk.model_dump_json() + "\n")


def load_chunks(path: Path) -> list[Chunk]:
    with path.open(encoding="utf-8") as f:
        return [Chunk.model_validate_json(line) for line in f if line.strip()]
