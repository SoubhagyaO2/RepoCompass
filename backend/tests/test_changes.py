from pathlib import Path

from app.indexing.changes import (
    ChangeSet,
    diff_manifest,
    file_order_key,
    scan_python_files,
    sha256_bytes,
)
from app.ingestion.discovery import discover_files


def test_diff_classifies_added_modified_deleted_and_unchanged() -> None:
    old = {"a.py": "1", "b.py": "2", "c.py": "3"}
    new = {"a.py": "1", "b.py": "CHANGED", "d.py": "4"}
    change = diff_manifest(old, new)
    assert change == ChangeSet(
        added=["d.py"], modified=["b.py"], deleted=["c.py"], unchanged=["a.py"]
    )
    assert change.changed == ["d.py", "b.py"] and not change.is_empty
    assert diff_manifest(old, old).is_empty


def test_scan_hashes_content_not_timestamps(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    # write_bytes, not write_text: on Windows write_text would translate "\n" to "\r\n"
    (tmp_path / "pkg" / "a.py").write_bytes(b"x = 1\n")
    (tmp_path / "README.md").write_text("not python")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "skip.py").write_bytes(b"ignored = True\n")

    first = scan_python_files(tmp_path)
    assert list(first) == ["pkg/a.py"]  # only Python, ignored directories skipped
    assert first["pkg/a.py"] == sha256_bytes(b"x = 1\n")

    (tmp_path / "pkg" / "a.py").write_bytes(b"x = 1\n")  # rewritten: new mtime, same bytes
    assert diff_manifest(first, scan_python_files(tmp_path)).is_empty
    (tmp_path / "pkg" / "a.py").write_bytes(b"x = 2\n")
    assert diff_manifest(first, scan_python_files(tmp_path)).modified == ["pkg/a.py"]
    # line endings are content: the same text with CRLF is a modification
    (tmp_path / "pkg" / "a.py").write_bytes(b"x = 1\r\n")
    assert diff_manifest(first, scan_python_files(tmp_path)).modified == ["pkg/a.py"]


def test_file_order_key_matches_the_order_discovery_lists_files(tmp_path: Path) -> None:
    for rel in ["z.py", "a/b.py", "a.py", "a/c/d.py", "a/c.py", "b/__init__.py", "B.py", "a/aa.py"]:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x = 1\n")
    discovered = [f.path for f in discover_files(tmp_path)]
    assert sorted(discovered, key=file_order_key) == discovered
    # a file sorts before any subdirectory of the same directory, unlike a plain string sort
    assert discovered.index("a.py") < discovered.index("a/aa.py")
    assert sorted(discovered) != discovered
