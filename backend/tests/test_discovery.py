from pathlib import Path

import pytest

from app.ingestion.discovery import discover_files
from app.ingestion.languages import detect_language


def _touch(root: Path, rel: str, content: str = "x") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_discovers_files_and_skips_ignored_dirs(tmp_path: Path) -> None:
    for rel in [
        "app/main.py",
        "app/utils/helpers.py",
        "web/index.ts",
        "README.md",
        ".git/config",
        "node_modules/react/index.js",
        "venv/lib/site.py",
        ".venv/lib/site.py",
        "app/__pycache__/main.cpython-313.pyc",
        "dist/bundle.js",
        "build/lib/app.py",
        ".next/server.js",
        "mypkg.egg-info/PKG-INFO",
    ]:
        _touch(tmp_path, rel)

    paths = [f.path for f in discover_files(tmp_path)]

    assert paths == ["README.md", "app/main.py", "app/utils/helpers.py", "web/index.ts"]


def test_records_language_and_size(tmp_path: Path) -> None:
    _touch(tmp_path, "pkg/mod.py", "print('hi')\n")
    (f,) = discover_files(tmp_path)
    assert f.path == "pkg/mod.py"
    assert f.language == "Python"
    assert f.size_bytes == (tmp_path / "pkg/mod.py").stat().st_size


def test_skips_symlinks(tmp_path: Path) -> None:
    outside = tmp_path / "outside.py"
    outside.write_text("secret")
    repo = tmp_path / "repo"
    repo.mkdir()
    try:
        (repo / "link.py").symlink_to(outside)
    except OSError:
        pytest.skip("creating symlinks requires extra privileges on this platform")
    assert discover_files(repo) == []


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("a.py", "Python"),
        ("types.pyi", "Python"),
        ("App.JSX", "JavaScript"),
        ("x.tsx", "TypeScript"),
        ("README.md", None),
        ("Makefile", None),
    ],
)
def test_detect_language(path: str, expected: str | None) -> None:
    assert detect_language(path) == expected
