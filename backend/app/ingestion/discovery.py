"""Walk a cloned repository and list its files, skipping irrelevant directories."""

import os
from pathlib import Path

from app.ingestion.languages import detect_language
from app.models.repository import SourceFile

IGNORED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        "node_modules",
        "venv",
        ".venv",
        "__pycache__",
        "dist",
        "build",
        ".next",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    }
)


def is_ignored_dir(name: str) -> bool:
    return name in IGNORED_DIRS or name.endswith(".egg-info")


def discover_files(root: Path) -> list[SourceFile]:
    """Return every regular file under `root`, in a stable (sorted) order.

    Ignored directories are pruned before descending, so large trees like
    node_modules are never walked. Symlinks are skipped so we never read
    outside the repository.
    """
    files: list[SourceFile] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Mutating dirnames in place tells os.walk which subdirectories to visit.
        dirnames[:] = sorted(d for d in dirnames if not is_ignored_dir(d))
        for filename in sorted(filenames):
            path = Path(dirpath) / filename
            if path.is_symlink() or not path.is_file():
                continue
            files.append(
                SourceFile(
                    path=path.relative_to(root).as_posix(),
                    language=detect_language(path),
                    size_bytes=path.stat().st_size,
                )
            )
    return files
