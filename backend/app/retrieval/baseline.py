"""The naive RAG baseline: fixed-size chunks -> embeddings -> vector search.

python -m app.retrieval.baseline index  <github-url>
python -m app.retrieval.baseline search <github-url> "where is routing handled?" [-k 5]
"""

import argparse
import re
import sys
import time

from pydantic import BaseModel

from app.chunking.fixed_chunker import chunk_fixed
from app.config import Settings, get_settings
from app.ingestion.clone import CloneError
from app.ingestion.service import ingest_repository
from app.ingestion.url import InvalidRepoURLError, parse_github_url
from app.models.chunk import TextChunk
from app.models.repository import IngestedRepository
from app.models.search import SearchHit
from app.retrieval.embedder import Embedder, get_embedder
from app.retrieval.vector_store import VectorStore


class BaselineIndexStats(BaseModel):
    repository: str
    files: int
    chunks: int
    chunk_size: int
    chunk_overlap: int
    embedding_model: str
    device: str
    chunking_seconds: float
    embedding_seconds: float
    upsert_seconds: float
    total_seconds: float


def collection_name(repository: str) -> str:
    """Qdrant collection for the baseline index of "owner/name"."""
    return "fixed__" + re.sub(r"[^A-Za-z0-9_-]", "_", repository.replace("/", "__"))


class FixedChunkBaseline:
    def __init__(
        self,
        embedder: Embedder,
        store: VectorStore,
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
        show_progress: bool = False,
    ) -> None:
        self.embedder = embedder
        self.store = store
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.show_progress = show_progress

    def index(self, ingested: IngestedRepository) -> BaselineIndexStats:
        repository = ingested.ref.full_name
        t0 = time.perf_counter()

        chunks: list[TextChunk] = []
        files = 0
        for f in ingested.files:
            if f.language != "Python":
                continue
            text = (ingested.local_path / f.path).read_bytes().decode("utf-8", errors="replace")
            chunks.extend(
                chunk_fixed(text, f.path, repository, self.chunk_size, self.chunk_overlap)
            )
            files += 1
        t1 = time.perf_counter()

        # Naive on purpose: the embedded text is the raw window, nothing else.
        vectors = (
            self.embedder.embed_documents([c.content for c in chunks], self.show_progress)
            if chunks
            else []
        )
        t2 = time.perf_counter()

        name = collection_name(repository)
        self.store.recreate_collection(name, self.embedder.dimension)
        self.store.upsert(name, vectors, [c.model_dump() for c in chunks])
        t3 = time.perf_counter()

        return BaselineIndexStats(
            repository=repository,
            files=files,
            chunks=len(chunks),
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            embedding_model=self.embedder.model_name,
            device=self.embedder.device,
            chunking_seconds=t1 - t0,
            embedding_seconds=t2 - t1,
            upsert_seconds=t3 - t2,
            total_seconds=t3 - t0,
        )

    def is_indexed(self, repository: str) -> bool:
        return self.store.collection_exists(collection_name(repository))

    def search(self, repository: str, query: str, k: int = 5) -> list[SearchHit]:
        results = self.store.search(
            collection_name(repository), self.embedder.embed_query(query), k
        )
        return [
            SearchHit(
                file_path=p["file_path"],
                start_line=p["start_line"],
                end_line=p["end_line"],
                score=score,
                content=p["content"],
                category=p["category"],
            )
            for score, p in results
        ]


# --- command line --------------------------------------------------------------------


def _format_stats(s: BaselineIndexStats) -> str:
    rate = s.chunks / s.embedding_seconds if s.embedding_seconds else 0
    return "\n".join(
        [
            f"Repository: {s.repository}",
            f"Indexed {s.files:,} Python files -> {s.chunks:,} fixed-size chunks "
            f"({s.chunk_size} chars, {s.chunk_overlap} overlap)",
            f"Model: {s.embedding_model} on {s.device}",
            "",
            f"Chunking:   {s.chunking_seconds:7.2f}s",
            f"Embedding:  {s.embedding_seconds:7.2f}s  ({rate:,.0f} chunks/s)",
            f"Upsert:     {s.upsert_seconds:7.2f}s",
            f"Total:      {s.total_seconds:7.2f}s",
        ]
    )


def _format_hit(rank: int, hit: SearchHit) -> str:
    header = (
        f"{rank}. score {hit.score:.3f}  {hit.file_path}:{hit.start_line}-{hit.end_line}  "
        f"({hit.category})"
    )
    preview = [line for line in hit.content.splitlines() if line.strip()][:4]
    return header + "\n" + "\n".join("     | " + line[:100] for line in preview)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.retrieval.baseline", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p_index = sub.add_parser("index", help="build the baseline index for a repository")
    p_index.add_argument("url")
    p_search = sub.add_parser("search", help="query an indexed repository")
    p_search.add_argument("url")
    p_search.add_argument("query")
    p_search.add_argument("-k", type=int, default=5, help="number of results (default 5)")
    args = parser.parse_args(argv)

    settings: Settings = get_settings()
    try:
        ingested = ingest_repository(args.url, settings) if args.command == "index" else None
        repository = ingested.ref.full_name if ingested else parse_github_url(args.url).full_name
    except (InvalidRepoURLError, CloneError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    with VectorStore(settings.qdrant_path) as store:
        if ingested is None and not store.collection_exists(collection_name(repository)):
            print(
                f"error: {repository} is not indexed; run the `index` command first",
                file=sys.stderr,
            )
            return 1
        baseline = FixedChunkBaseline(
            get_embedder(settings),
            store,
            settings.baseline_chunk_size,
            settings.baseline_chunk_overlap,
            show_progress=True,
        )
        if ingested is not None:
            stats = baseline.index(ingested)
            out = (
                settings.index_dir / ingested.ref.owner / ingested.ref.name / "baseline_stats.json"
            )
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(stats.model_dump_json(indent=2), encoding="utf-8")
            print("\n" + _format_stats(stats))
            print(f"\nStats saved to {out}")
        else:
            start = time.perf_counter()
            hits = baseline.search(repository, args.query, args.k)
            elapsed = time.perf_counter() - start
            print(f'\nQuery: "{args.query}"  ({elapsed * 1000:.0f} ms)\n')
            for rank, hit in enumerate(hits, 1):
                print(_format_hit(rank, hit) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
