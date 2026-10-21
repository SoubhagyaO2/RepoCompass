"""Connecting repositories by URL: validation, one background indexing worker, and the set of
repositories the API can serve.

The job reuses the existing pipeline (hardened clone -> LiveIndex.build_full -> EvidenceBuilder);
nothing here indexes on its own. Safety rules, each covered by a test:

- only https://github.com/<owner>/<repo> is accepted, and the clone URL is rebuilt from the
  validated parts, so no user text reaches git or the network layer (no SSRF);
- every repository has its own id, clone directory, state directory and vector collection, and a
  request can only create or retry its own id: it never rebuilds or replaces another repository;
- clones are limited (time, size, number of Python files), the queue is bounded, and the number of
  repositories is capped without evicting anyone;
- status and errors never contain server paths or secrets.
"""

import logging
import shutil
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.answer.prompt_product import PRODUCT_PROMPT
from app.answer.service import AnswerService
from app.config import Settings
from app.evidence.builder import EvidenceBuilder
from app.indexing.live_index import IndexStateError, LiveIndex
from app.ingestion.clone import CloneError, clone_repository, directory_size_mb
from app.ingestion.discovery import discover_files
from app.ingestion.url import InvalidRepoURLError, parse_github_url
from app.llm.base import LLMClient
from app.models.repository import RepoRef
from app.retrieval.embedder import Embedder
from app.retrieval.vector_store import VectorStore
from app.runtime import memory_limit_mb

log = logging.getLogger("repocompass.indexer")
if not log.handlers:  # uvicorn configures only its own loggers; make ours visible in the host log
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    log.addHandler(_handler)
    log.setLevel(logging.INFO)

ID_PREFIX = "gh__"
ACTIVE = ("queued", "cloning", "indexing")
ERROR_LIMIT = 300


class IndexingDisabled(RuntimeError):
    """Indexing from the API is switched off (REPOCOMPASS_ENABLE_REPO_INDEXING=false)."""


class RegistryFull(RuntimeError):
    """The cap on repositories is reached; nothing is evicted to make room."""


class QueueFull(RuntimeError):
    """Too many indexing jobs are running or waiting."""


class RepositoryNotFound(LookupError):
    pass


class RepositoryNotReady(RuntimeError):
    pass


def repository_id(owner: str, name: str) -> str:
    """`gh__owner__name`, lowercase (GitHub names are case-insensitive). Owners cannot contain an
    underscore, so the first `__` after the prefix always separates owner from name."""
    return f"{ID_PREFIX}{owner.lower()}__{name.lower()}"


def split_id(rid: str) -> tuple[str, str] | None:
    if not rid.startswith(ID_PREFIX):
        return None
    owner, sep, name = rid[len(ID_PREFIX) :].partition("__")
    return (owner, name) if sep and owner and name else None


def validate_url(url: str) -> RepoRef:
    """Strict public-GitHub validation on top of `parse_github_url`: https only, no credentials,
    no port. Raises InvalidRepoURLError."""
    text = (url or "").strip()
    if not text.lower().startswith("https://"):
        raise InvalidRepoURLError("The URL must start with https://github.com/")
    parsed = urlparse(text)
    if parsed.username or parsed.password or parsed.port:
        raise InvalidRepoURLError("The URL must not contain credentials or a port")
    return parse_github_url(text)


@dataclass
class RepoEntry:
    id: str
    name: str
    status: str = "queued"
    error: str | None = None
    files: int | None = None
    chunks: int | None = None
    files_total: int | None = None
    default: bool = False
    builder: EvidenceBuilder | None = field(default=None, repr=False)
    service: AnswerService | None = field(default=None, repr=False)
    future: Future[None] | None = field(default=None, repr=False)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "error": self.error,
            "files": self.files,
            "chunks": self.chunks,
            "files_total": self.files_total,
            "default": self.default,
        }


class RepositoryRegistry:
    def __init__(
        self,
        settings: Settings,
        store: VectorStore | None = None,
        embedder: Embedder | None = None,
        llm: LLMClient | None = None,
        clone: Callable[..., Path] = clone_repository,
        memory_limit: Callable[[], int | None] = memory_limit_mb,
    ) -> None:
        self._settings = settings
        self._store_ = store
        self._embedder_ = embedder
        self._owns_store = False
        self._llm = llm
        self._clone = clone
        self._memory_limit = memory_limit
        self._entries: dict[str, RepoEntry] = {}
        self._default: str | None = None
        self._lock = threading.RLock()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="indexer")

    # The vector store and the embedding model are created on first use, so an API that never
    # indexes a repository (and the tests that build the app) pay nothing for them.
    @property
    def _store(self) -> VectorStore:
        with self._lock:
            if self._store_ is None:
                self._store_ = VectorStore(self._settings.live_qdrant_path)
                self._owns_store = True
            return self._store_

    @property
    def _embedder(self) -> Embedder:
        with self._lock:
            if self._embedder_ is None:
                from app.retrieval.embedder import get_embedder

                self._embedder_ = get_embedder(self._settings)
            return self._embedder_

    def adopt(self, store: VectorStore, embedder: Embedder, owns_store: bool = False) -> None:
        """Share a store and embedder that were already opened (the configured default index)."""
        with self._lock:
            self._store_, self._embedder_, self._owns_store = store, embedder, owns_store

    # --- the default (configured) index ---

    def register_default(self, rid: str, name: str, builder: EvidenceBuilder) -> None:
        with self._lock:
            entry = RepoEntry(rid, name, "ready", default=True, builder=builder)
            if getattr(builder, "index", None) is not None:  # a test double may have no index
                info = builder.index_info()
                entry.files, entry.chunks = info.files, info.chunks
            entry.service = self._service_for(builder)
            self._entries[rid] = entry
            self._default = rid

    def _service_for(self, builder: EvidenceBuilder) -> AnswerService | None:
        if self._llm is None:
            return None
        return AnswerService(
            lambda q, k: builder.build(q, k, include_text=True),
            self._llm,
            system_prompt=PRODUCT_PROMPT,
            structured_output=self._settings.structured_output,
        )

    # --- submitting ---

    def submit(self, url: str) -> RepoEntry:
        if not self._settings.enable_repo_indexing:
            raise IndexingDisabled("Indexing repositories from the API is disabled on this server")
        ref = validate_url(url)
        rid = repository_id(ref.owner, ref.name)
        with self._lock:
            existing = self._entries.get(rid)
            if existing and existing.status != "failed":
                return existing  # idempotent: never rebuilds or replaces an existing repository
            active = sum(1 for e in self._entries.values() if e.status in ACTIVE)
            if active >= self._settings.max_queued_jobs:
                raise QueueFull("Too many repositories are being indexed; try again shortly")
            kept = sum(1 for e in self._entries.values() if e.id != rid and e.status != "failed")
            if kept >= self._settings.max_repositories:
                raise RegistryFull(
                    f"This server already holds {kept} repositories (the limit); none is removed"
                )
            entry = RepoEntry(rid, f"{ref.owner}/{ref.name}".lower())
            self._entries[rid] = entry
            entry.future = self._pool.submit(self._run, entry, ref)
            return entry

    # --- the job ---

    def _fail(self, entry: RepoEntry, message: str, dest: Path | None) -> None:
        text = message
        for secret in (str(self._settings.data_dir.resolve()), str(self._settings.data_dir)):
            text = text.replace(secret, "<data>")
        entry.error = text[:ERROR_LIMIT]
        started_indexing = entry.status == "indexing"
        entry.status = "failed"
        log.warning("%s failed: %s", entry.id, entry.error)
        self._cleanup(entry.id, dest, drop_collections=started_indexing)

    def _cleanup(self, rid: str, dest: Path | None, drop_collections: bool) -> None:
        if dest is not None:
            shutil.rmtree(dest, ignore_errors=True)
        shutil.rmtree(self._settings.live_state_dir(rid), ignore_errors=True)
        if not drop_collections:
            return  # nothing is in the vector store yet (and no model needs loading)
        try:
            probe = self._index(rid, dest or self._settings.repos_dir)
            for view in ("ast", "fixed"):
                self._store.drop_collection(probe.collection(view))
        except Exception:  # noqa: BLE001  (cleanup must never mask the original failure)
            pass

    def _index(self, rid: str, root: Path) -> LiveIndex:
        return LiveIndex(
            rid,
            root,
            self._settings.live_state_dir(rid),
            self._store,
            self._embedder,
            self._settings.baseline_chunk_size,
            self._settings.baseline_chunk_overlap,
        )

    def _run(self, entry: RepoEntry, ref: RepoRef) -> None:
        dest = self._settings.repos_dir / ref.owner.lower() / ref.name.lower()
        try:
            limit = self._memory_limit()
            needed = self._settings.min_memory_mb
            if needed and limit is not None and limit < needed:
                self._fail(
                    entry,
                    f"This server has {limit} MiB of memory; indexing needs about {needed} MiB "
                    "(it loads an embedding model). Run the API with more memory.",
                    None,
                )
                return
            entry.status = "cloning"
            log.info("%s cloning", entry.id)
            self._clone(
                ref.clone_url, dest, timeout_s=self._settings.clone_timeout_s, hardened=True
            )
            size = directory_size_mb(dest)
            if size > self._settings.max_clone_mb:
                self._fail(
                    entry,
                    f"The repository is {size:.0f} MiB, over the "
                    f"{self._settings.max_clone_mb} MiB limit",
                    dest,
                )
                return
            python = [f for f in discover_files(dest) if f.language == "Python"]
            entry.files_total = len(python)
            if not python:
                self._fail(entry, "No Python files found in this repository", dest)
                return
            if len(python) > self._settings.max_python_files:
                self._fail(
                    entry,
                    f"The repository has {len(python)} Python files, over the "
                    f"{self._settings.max_python_files} limit",
                    dest,
                )
                return
            entry.status = "indexing"
            log.info("%s indexing %d Python files", entry.id, len(python))
            index = self._index(entry.id, dest)
            index.build_full()
            builder = EvidenceBuilder(index)
            info = builder.index_info()
            entry.files, entry.chunks = info.files, info.chunks
            entry.builder = builder
            entry.service = self._service_for(builder)
            entry.status = "ready"
            log.info("%s ready: %s files, %s chunks", entry.id, entry.files, entry.chunks)
        except CloneError as error:
            self._fail(entry, str(error), dest)
        except Exception as error:  # noqa: BLE001  (a job must always end in a visible state)
            log.exception("%s: unexpected error while indexing", entry.id)
            self._fail(entry, f"Indexing failed ({type(error).__name__})", dest)

    # --- lookup ---

    def wait(self, rid: str, timeout: float = 120) -> None:
        entry = self._entries[rid]
        if entry.future is not None:
            entry.future.result(timeout)

    def get(self, rid: str) -> RepoEntry | None:
        return self._entries.get(rid)

    def list(self) -> list[RepoEntry]:
        with self._lock:
            return list(self._entries.values())

    def _resolve(self, rid: str | None) -> RepoEntry:
        key = rid or self._default
        entry = self._entries.get(key) if key else None
        if entry is None:
            raise RepositoryNotFound(
                "Unknown repository" if rid else "No repository is selected or configured"
            )
        if entry.status != "ready" or entry.builder is None:
            raise RepositoryNotReady(f"{entry.name} is not ready ({entry.status})")
        return entry

    def builder(self, rid: str | None) -> EvidenceBuilder:
        return self._resolve(rid).builder  # type: ignore[return-value]

    def service(self, rid: str | None) -> AnswerService | None:
        return self._resolve(rid).service

    @property
    def indexing_enabled(self) -> bool:
        return self._settings.enable_repo_indexing

    @property
    def default_id(self) -> str | None:
        return self._default

    def any_ready(self) -> bool:
        return any(e.status == "ready" for e in self._entries.values())

    # --- restart ---

    def load_existing(self) -> None:
        """Reload repositories indexed through the API (and only those: ids with the prefix)."""
        base = self._settings.data_dir / "live_state"
        if not base.is_dir() or not any(split_id(p.name) for p in base.iterdir()):
            return
        for path in sorted(base.iterdir()):
            parts = split_id(path.name)
            if parts is None or not (path / "manifest.json").exists():
                continue
            owner, name = parts
            root = self._settings.repos_dir / owner / name
            entry = RepoEntry(path.name, f"{owner}/{name}")
            try:
                index = self._index(path.name, root)
                if not index.load():
                    continue
                builder = EvidenceBuilder(index)
                info = builder.index_info()
                entry.files, entry.chunks = info.files, info.chunks
                entry.builder, entry.service, entry.status = (
                    builder,
                    self._service_for(builder),
                    "ready",
                )
            except (IndexStateError, OSError, ValueError) as error:
                entry.status, entry.error = (
                    "failed",
                    f"Stored index unusable: {error}"[:ERROR_LIMIT],
                )
            self._entries[entry.id] = entry

    def shutdown(self) -> None:
        self._pool.shutdown(wait=True, cancel_futures=True)
        if self._owns_store and self._store_ is not None:
            self._store_.close()
