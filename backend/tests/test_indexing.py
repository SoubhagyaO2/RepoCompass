from pathlib import Path

from app.indexing.service import build_index, compute_stats, load_chunks, save_chunks
from app.ingestion.discovery import discover_files
from app.models.repository import IngestedRepository, RepoRef


def make_repo(tmp_path: Path) -> IngestedRepository:
    files = {
        "pkg/__init__.py": "from .core import run as run\n",
        "pkg/core.py": "class Engine:\n    def go(self):\n        pass\n\n\ndef run():\n    pass\n",
        "tests/test_core.py": "def test_run():\n    assert True\n",
        "pkg/broken.py": "def broken(:\n    pass\n",
        "README.md": "# not python",
        "web/app.js": "function f() {}",
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    return IngestedRepository(
        ref=RepoRef(owner="acme", name="widget"),
        local_path=tmp_path,
        files=discover_files(tmp_path),
    )


def test_build_index_counts_only_python_files(tmp_path: Path) -> None:
    chunks, stats = build_index(make_repo(tmp_path))

    assert stats.repository == "acme/widget"
    assert stats.files_indexed == 4
    assert stats.files_with_syntax_errors == 1
    assert stats.chunks_by_type["class"] == 1
    assert stats.chunks_by_type["method"] == 1
    assert stats.chunks_by_type["function"] >= 2  # run, test_run (+ whatever survives in broken)
    assert stats.chunks_by_type["module"] == 1  # the __init__.py re-export
    assert stats.chunks_by_category["test"] == 1
    assert {c.file_path for c in chunks} <= {
        "pkg/__init__.py",
        "pkg/core.py",
        "tests/test_core.py",
        "pkg/broken.py",
    }


def test_compute_stats_size_distribution() -> None:
    stats = compute_stats("a/b", [], files=0, errors=0, seconds=0.0)
    assert (stats.chunk_chars.median, stats.chunk_chars.p95, stats.chunk_chars.max) == (0, 0, 0)
    assert stats.large_chunks == 0


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    chunks, _ = build_index(make_repo(tmp_path / "repo"))
    out = tmp_path / "out" / "chunks.jsonl"
    save_chunks(chunks, out)
    assert load_chunks(out) == chunks
