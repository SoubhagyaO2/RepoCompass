"""Detect what changed in a repository by content hash (never by timestamp)."""

import hashlib
from pathlib import Path

from pydantic import BaseModel

from app.ingestion.discovery import discover_files


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def scan_python_files(root: Path) -> dict[str, str]:
    """Repo-relative path -> SHA-256 of the file's bytes, for every Python file that ingestion
    would index (ignored directories such as .git and node_modules are skipped)."""
    return {
        f.path: sha256_bytes((root / f.path).read_bytes())
        for f in discover_files(root)
        if f.language == "Python"
    }


class ChangeSet(BaseModel):
    added: list[str]
    modified: list[str]
    deleted: list[str]
    unchanged: list[str]

    @property
    def changed(self) -> list[str]:
        """Files that must be (re)processed: added and modified."""
        return [*self.added, *self.modified]

    @property
    def is_empty(self) -> bool:
        return not (self.added or self.modified or self.deleted)


def diff_manifest(old: dict[str, str], new: dict[str, str]) -> ChangeSet:
    """Compare two path -> hash manifests. Lists are sorted for determinism."""
    return ChangeSet(
        added=sorted(p for p in new if p not in old),
        modified=sorted(p for p in new if p in old and new[p] != old[p]),
        deleted=sorted(p for p in old if p not in new),
        unchanged=sorted(p for p in new if p in old and new[p] == old[p]),
    )


def file_order_key(path: str) -> tuple[tuple[int, str], ...]:
    """Sort key that reproduces the order in which ingestion lists files.

    Discovery walks a directory's own files (sorted) before descending into its subdirectories
    (sorted), so within one directory a file sorts before any subdirectory. The static BM25 index
    breaks score ties by this corpus order; the live index must break them the same way for the
    two to be interchangeable.
    """
    parts = path.split("/")
    return (*((1, p) for p in parts[:-1]), (0, parts[-1]))
