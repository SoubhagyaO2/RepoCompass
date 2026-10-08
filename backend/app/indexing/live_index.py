"""The live index: a persistent index that is updated incrementally (Milestone 11).

It keeps, for both chunkings used in the benchmark (fixed windows and AST chunks): the chunk
records, an embedding collection, a BM25 index and, for the whole repository, the code graph.
`update()` finds what changed by content hash and touches only that. The design and the
correctness criterion are in evaluation/plans/m11-incremental-indexing.md: an incrementally
updated index must be identical to one built from scratch.

Write order (so an interrupted update is retried safely and never loses data): new points ->
per-file records -> stale points deleted -> manifest last. Every write is idempotent.
"""

import json
import os
import time
import uuid
from collections import defaultdict
from hashlib import sha1
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.chunking.ast_chunker import chunk_python_source
from app.chunking.fixed_chunker import chunk_fixed
from app.graph.builder import build_graph_from_parsed
from app.graph.model import CodeGraph
from app.indexing.changes import (
    diff_manifest,
    file_order_key,
    scan_python_files,
    sha256_bytes,
    sha256_text,
)
from app.models.chunk import Chunk, FileCategory, TextChunk
from app.models.search import SearchHit
from app.models.symbols import ParsedFile
from app.retrieval.bm25 import LiveBM25Index
from app.retrieval.embedder import Embedder
from app.retrieval.hybrid import HybridRetriever, SearchFn
from app.retrieval.vector_store import VectorStore

VIEWS = ("fixed", "ast")
FORMAT_VERSION = 2  # 2: imports carry their scope (stored parses of version 1 lack it)
POINT_NAMESPACE = uuid.UUID("6f2c8a40-7d1e-4b8e-9a53-2c5d4f0e1a77")


def point_id(chunk_id: str) -> str:
    """Stable vector-store point id of a chunk."""
    return str(uuid.uuid5(POINT_NAMESPACE, chunk_id))


class IndexStateError(RuntimeError):
    """The stored index cannot be used (configuration changed, or files are missing)."""


class LiveConfig(BaseModel):
    format_version: int = FORMAT_VERSION
    repository: str
    chunk_size: int
    chunk_overlap: int
    embedding_model: str
    embedding_dimension: int


class IndexedChunk(BaseModel):
    """A chunk of either chunking, in the shape stored on disk and in the vector store."""

    id: str
    file_path: str
    start_line: int
    end_line: int
    content: str
    content_sha: str
    category: FileCategory
    symbol_name: str | None = None
    symbol_type: str | None = None
    seq: int  # position within its file's chunks (tie-break order for BM25)

    def to_hit(self, score: float) -> SearchHit:
        return SearchHit(
            file_path=self.file_path,
            start_line=self.start_line,
            end_line=self.end_line,
            score=score,
            content=self.content,
            category=self.category,
            symbol_name=self.symbol_name,
            symbol_type=self.symbol_type,
        )


class FileRecord(BaseModel):
    path: str
    sha256: str
    size: int
    parsed: ParsedFile
    chunks: dict[str, list[IndexedChunk]]  # view -> chunks


class ViewStats(BaseModel):
    new_chunks: int = 0  # chunks of the added/modified files
    embedded_chunks: int = 0  # of those, chunks whose text had to go through the model
    reused_chunks: int = 0  # of those, chunks whose vector was found in the store
    model_texts: int = 0  # distinct texts actually sent to the model
    points_upserted: int = 0
    points_skipped: int = 0  # new chunks identical to the stored chunk: nothing to write
    points_deleted: int = 0


class UpdateStats(BaseModel):
    added: int
    modified: int
    deleted: int
    unchanged: int
    views: dict[str, ViewStats]
    graph_edges_added: int = 0
    graph_edges_removed: int = 0
    seconds: dict[str, float]


def _from_ast(c: Chunk, seq: int) -> IndexedChunk:
    return IndexedChunk(
        id=c.id, file_path=c.file_path, start_line=c.start_line, end_line=c.end_line,
        content=c.content, content_sha=sha256_text(c.content), category=c.category,
        symbol_name=c.qualified_name, symbol_type=c.symbol_type, seq=seq,
    )  # fmt: skip


def _from_fixed(c: TextChunk, seq: int) -> IndexedChunk:
    return IndexedChunk(
        id=c.id, file_path=c.file_path, start_line=c.start_line, end_line=c.end_line,
        content=c.content, content_sha=sha256_text(c.content), category=c.category, seq=seq,
    )  # fmt: skip


def _edge_set(graph: CodeGraph) -> set[tuple[str, str, str]]:
    return {(u, v, k) for u, v, k in graph.graph.edges(keys=True)}


class LiveIndex:
    def __init__(
        self,
        repository: str,
        root: Path,
        state_dir: Path,
        store: VectorStore,
        embedder: Embedder,
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
        show_progress: bool = False,
    ) -> None:
        self.repository = repository
        self.root = root
        self.state_dir = state_dir
        self.store = store
        self.embedder = embedder
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.show_progress = show_progress
        self.config = LiveConfig(
            repository=repository, chunk_size=chunk_size, chunk_overlap=chunk_overlap,
            embedding_model=embedder.model_name, embedding_dimension=embedder.dimension,
        )  # fmt: skip

        self.records: dict[str, FileRecord] = {}
        self.chunks: dict[str, dict[str, IndexedChunk]] = {v: {} for v in VIEWS}
        self.sha_ids: dict[str, dict[str, set[str]]] = {v: defaultdict(set) for v in VIEWS}
        self.bm25: dict[str, LiveBM25Index] = {v: LiveBM25Index() for v in VIEWS}
        self.graph: CodeGraph | None = None

    # --- naming and paths ----------------------------------------------------------------

    def collection(self, view: str) -> str:
        safe = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in self.repository)
        return f"live_{view}__{safe}"

    @property
    def _manifest_path(self) -> Path:
        return self.state_dir / "manifest.json"

    def _record_path(self, path: str, sha: str) -> Path:
        """One file per (path, content) version, so the version a manifest points at is never
        overwritten while a newer one is being written."""
        return self.state_dir / "files" / f"{sha1(path.encode()).hexdigest()[:24]}-{sha[:20]}.json"

    # --- lifecycle -----------------------------------------------------------------------

    def load(self) -> bool:
        """Load a stored index. False if none exists; IndexStateError if it cannot be used."""
        if not self._manifest_path.exists():
            return False
        manifest = json.loads(self._manifest_path.read_text(encoding="utf-8"))
        stored = LiveConfig.model_validate(manifest["config"])
        if stored != self.config:
            raise IndexStateError(
                f"the stored index was built with {stored.model_dump()}, "
                f"this one is configured with {self.config.model_dump()}; rebuild it"
            )
        for view in VIEWS:
            if not self.store.collection_exists(self.collection(view)):
                raise IndexStateError(f"vector collection {self.collection(view)} is missing")
        self._clear_memory()
        for path, sha in manifest["files"].items():
            file = self._record_path(path, sha)
            if not file.exists():
                raise IndexStateError(f"record for {path} is missing")
            record = FileRecord.model_validate_json(file.read_text(encoding="utf-8"))
            if record.sha256 != sha:
                raise IndexStateError(f"record for {path} does not match the manifest")
            self._register(record)
        self.graph = build_graph_from_parsed({p: r.parsed for p, r in self.records.items()})
        self._repair_store()
        return True

    def reset(self) -> None:
        """Delete everything stored and start empty."""
        for view in VIEWS:
            self.store.drop_collection(self.collection(view))
            self.store.ensure_collection(self.collection(view), self.embedder.dimension)
        if self.state_dir.exists():
            for file in sorted(self.state_dir.rglob("*"), reverse=True):
                file.unlink() if file.is_file() else file.rmdir()
        self._clear_memory()

    def _clear_memory(self) -> None:
        self.records = {}
        self.chunks = {v: {} for v in VIEWS}
        self.sha_ids = {v: defaultdict(set) for v in VIEWS}
        self.bm25 = {v: LiveBM25Index() for v in VIEWS}
        self.graph = None

    def build_full(self) -> UpdateStats:
        """Build from scratch: the cold path (everything is 'added')."""
        self.reset()
        return self.update()

    # --- in-memory bookkeeping -----------------------------------------------------------

    def _register(self, record: FileRecord) -> None:
        self.records[record.path] = record
        order = file_order_key(record.path)
        for view in VIEWS:
            for c in record.chunks[view]:
                self.chunks[view][c.id] = c
                self.sha_ids[view][c.content_sha].add(c.id)
                self.bm25[view].add(c.id, c.content, (order, c.seq))

    def _unregister(self, path: str) -> None:
        record = self.records.pop(path)
        for view in VIEWS:
            for c in record.chunks[view]:
                self.chunks[view].pop(c.id, None)
                ids = self.sha_ids[view].get(c.content_sha)
                if ids is not None:
                    ids.discard(c.id)
                    if not ids:
                        del self.sha_ids[view][c.content_sha]
                if c.id in self.bm25[view]:
                    self.bm25[view].remove(c.id)

    def _make_record(self, path: str) -> FileRecord:
        data = (self.root / path).read_bytes()
        parsed, ast_chunks = chunk_python_source(data, path, self.repository)
        text = data.decode("utf-8", errors="replace")
        fixed = chunk_fixed(text, path, self.repository, self.chunk_size, self.chunk_overlap)
        return FileRecord(
            path=path,
            sha256=sha256_bytes(data),
            size=len(data),
            parsed=parsed,
            chunks={
                "fixed": [_from_fixed(c, i) for i, c in enumerate(fixed)],
                "ast": [_from_ast(c, i) for i, c in enumerate(ast_chunks)],
            },
        )

    # --- the update ----------------------------------------------------------------------

    def update(self) -> UpdateStats:
        """Bring the index in line with the files on disk, touching only what changed."""
        seconds: dict[str, float] = defaultdict(float)
        started = time.perf_counter()

        def lap(name: str, since: float) -> None:
            seconds[name] += time.perf_counter() - since

        t = time.perf_counter()
        current = scan_python_files(self.root)
        change = diff_manifest({p: r.sha256 for p, r in self.records.items()}, current)
        lap("scan", t)
        stats = UpdateStats(
            added=len(change.added), modified=len(change.modified),
            deleted=len(change.deleted), unchanged=len(change.unchanged),
            views={v: ViewStats() for v in VIEWS}, seconds={},
        )  # fmt: skip
        if change.is_empty and self.graph is not None:
            seconds["total"] = time.perf_counter() - started
            stats.seconds = dict(seconds)
            return stats

        for view in VIEWS:
            self.store.ensure_collection(self.collection(view), self.embedder.dimension)

        t = time.perf_counter()
        new_records = {path: self._make_record(path) for path in change.changed}
        lap("parse_chunk", t)

        # Chunks identical (every field) to the one already stored under the same id need no
        # write at all; only the others get a point, so only they need a vector.
        new_chunks = {v: [c for r in new_records.values() for c in r.chunks[v]] for v in VIEWS}
        to_write = {v: [c for c in new_chunks[v] if self.chunks[v].get(c.id) != c] for v in VIEWS}
        for view in VIEWS:
            vs = stats.views[view]
            vs.new_chunks = len(new_chunks[view])
            vs.points_skipped = vs.new_chunks - len(to_write[view])

        # Vectors first, while every old point is still in the store (reuse reads them).
        vectors: dict[str, dict[str, list[float]]] = {}
        for view in VIEWS:
            vectors[view] = self._vectors_for(view, to_write[view], stats.views[view], seconds)
            stats.views[view].reused_chunks = (
                stats.views[view].new_chunks - stats.views[view].embedded_chunks
            )

        old_ids = {v: self._chunk_ids_of(change.modified + change.deleted, v) for v in VIEWS}

        for view in VIEWS:  # 1. new and changed points
            t = time.perf_counter()
            self.store.upsert_points(
                self.collection(view),
                [point_id(c.id) for c in to_write[view]],
                [vectors[view][c.id] for c in to_write[view]],
                [c.model_dump() for c in to_write[view]],
            )
            lap("store_write", t)
            stats.views[view].points_upserted = len(to_write[view])

        t = time.perf_counter()  # 2. per-file records
        (self.state_dir / "files").mkdir(parents=True, exist_ok=True)
        for record in new_records.values():
            self._record_path(record.path, record.sha256).write_text(
                record.model_dump_json(), encoding="utf-8"
            )
        lap("persist", t)

        for view in VIEWS:  # 3. stale points
            fresh = {c.id for c in new_chunks[view]}
            stale = sorted(old_ids[view] - fresh)
            t = time.perf_counter()
            self.store.delete_points(self.collection(view), [point_id(i) for i in stale])
            lap("store_delete", t)
            stats.views[view].points_deleted = len(stale)

        t = time.perf_counter()  # in-memory structures: BM25, maps
        for path in change.modified + change.deleted:
            self._unregister(path)
        for record in new_records.values():
            self._register(record)
        lap("bm25", t)

        t = time.perf_counter()  # the graph: stored parses of unchanged files + new parses
        before = _edge_set(self.graph) if self.graph is not None else set()
        self.graph = build_graph_from_parsed({p: r.parsed for p, r in self.records.items()})
        after = _edge_set(self.graph)
        stats.graph_edges_added, stats.graph_edges_removed = (
            len(after - before),
            len(before - after),
        )
        lap("graph", t)

        t = time.perf_counter()  # 4. manifest, last
        self._write_manifest()
        self._remove_orphan_records()
        lap("persist", t)

        seconds["total"] = time.perf_counter() - started
        stats.seconds = dict(seconds)
        return stats

    def _chunk_ids_of(self, paths: list[str], view: str) -> set[str]:
        return {c.id for p in paths if p in self.records for c in self.records[p].chunks[view]}

    def _vectors_for(
        self, view: str, new_chunks: list[IndexedChunk], vs: ViewStats, seconds: dict[str, float]
    ) -> dict[str, list[float]]:
        """chunk id -> vector for the given chunks, embedding only texts new to the store."""
        source: dict[str, str] = {}  # content hash -> an existing chunk id with that text
        for c in new_chunks:
            existing = self.sha_ids[view].get(c.content_sha)
            if existing and c.content_sha not in source:
                source[c.content_sha] = min(existing)

        t = time.perf_counter()
        stored = self.store.get_vectors(
            self.collection(view), [point_id(cid) for cid in source.values()]
        )
        seconds["store_read"] += time.perf_counter() - t
        by_sha = {
            sha: stored[point_id(cid)] for sha, cid in source.items() if point_id(cid) in stored
        }

        to_embed: dict[str, str] = {}
        for c in new_chunks:
            if c.content_sha not in by_sha and c.content_sha not in to_embed:
                to_embed[c.content_sha] = c.content
        t = time.perf_counter()
        if to_embed:
            embedded = self.embedder.embed_documents(list(to_embed.values()), self.show_progress)
            by_sha.update(zip(to_embed, embedded, strict=True))
        seconds["embed"] += time.perf_counter() - t

        vs.model_texts = len(to_embed)
        vs.embedded_chunks = sum(1 for c in new_chunks if c.content_sha in to_embed)
        return {c.id: by_sha[c.content_sha] for c in new_chunks}

    def _remove_orphan_records(self) -> None:
        """Delete record files no manifest entry refers to (old versions, deleted files, the
        leftovers of an interrupted update)."""
        keep = {self._record_path(p, r.sha256).name for p, r in self.records.items()}
        for file in (self.state_dir / "files").glob("*.json"):
            if file.name not in keep:
                file.unlink()

    def _repair_store(self) -> None:
        """Remove vector points no stored record accounts for (an interrupted update may have
        written new points before it could write its manifest). Missing points mean corruption."""
        for view in VIEWS:
            expected = {point_id(cid) for cid in self.chunks[view]}
            present = self.store.point_ids(self.collection(view))
            if expected - present:
                raise IndexStateError(
                    f"{len(expected - present)} points of the {view} index are missing; rebuild it"
                )
            self.store.delete_points(self.collection(view), sorted(present - expected))

    def _write_manifest(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "config": self.config.model_dump(),
            "files": {p: r.sha256 for p, r in sorted(self.records.items())},
        }
        tmp = self._manifest_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
        os.replace(tmp, self._manifest_path)  # atomic: the manifest is either old or new

    # --- searching -----------------------------------------------------------------------

    def bm25_search(self, view: str) -> SearchFn:
        def search(query: str, depth: int) -> list[SearchHit]:
            return [
                self.chunks[view][doc_id].to_hit(score)
                for doc_id, score in self.bm25[view].search(query, depth)
            ]

        return search

    def embedding_search(self, view: str) -> SearchFn:
        def search(query: str, depth: int) -> list[SearchHit]:
            results = self.store.search(
                self.collection(view), self.embedder.embed_query(query), depth
            )
            return [SearchHit.model_validate({**payload, "score": s}) for s, payload in results]

        return search

    def hybrid(self, view: str) -> SearchFn:
        """BM25 + embeddings fused with RRF: the Milestone 8 first stage, over the live index."""
        return HybridRetriever([self.bm25_search(view), self.embedding_search(view)]).search

    # --- verification --------------------------------------------------------------------

    def snapshot(self, probe_queries: list[str], depth: int = 50) -> dict[str, Any]:
        """Everything the correctness criterion compares, in a canonical, comparable form
        (vectors are checked separately, see `vectors`)."""
        graph = self.graph
        assert graph is not None
        return {
            "files": {p: r.sha256 for p, r in sorted(self.records.items())},
            "chunks": {
                v: sorted(
                    (c.id, c.content_sha, c.start_line, c.end_line) for c in self.chunks[v].values()
                )
                for v in VIEWS
            },
            "points": {
                v: sorted(
                    (pid, json.dumps(payload, sort_keys=True))
                    for pid, (payload, _) in self.store.all_points(self.collection(v)).items()
                )
                for v in VIEWS
            },
            "bm25": {v: [self.bm25[v].search(q, depth) for q in probe_queries] for v in VIEWS},
            "graph_nodes": sorted(graph.graph.nodes),
            "graph_edges": sorted(
                (
                    u,
                    w,
                    k,
                    tuple(d.get("lines") or [d.get("line")]),
                    tuple(d.get("rules") or [d.get("rule")]),
                )
                for u, w, k, d in graph.graph.edges(keys=True, data=True)
            ),
        }

    def vectors(self, view: str) -> dict[str, list[float]]:
        """chunk id -> stored vector, for every chunk of a view."""
        points = self.store.all_points(self.collection(view), with_vectors=True)
        return {payload["id"]: vector for payload, vector in points.values() if vector is not None}
