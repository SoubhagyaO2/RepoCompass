"""Classify files as library source, tests, or documentation/examples by path."""

from pathlib import PurePosixPath

from app.models.chunk import FileCategory

_TEST_DIRS = {"tests", "test", "testing"}
_DOCS_DIRS = {"docs", "doc", "docs_src", "examples", "example"}


def classify_file(path: str) -> FileCategory:
    """Classify a repo-relative POSIX path. Tests win over docs (e.g. docs/tests/...)."""
    p = PurePosixPath(path)
    dirs = {part.lower() for part in p.parts[:-1]}
    name = p.name.lower()
    if dirs & _TEST_DIRS or name.startswith("test_") or name.endswith("_test.py"):
        return "test"
    if name == "conftest.py":
        return "test"
    if dirs & _DOCS_DIRS:
        return "docs"
    return "source"
