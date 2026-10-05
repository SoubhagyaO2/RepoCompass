"""Clone tests use a local git repository as the source, so they run offline."""

import subprocess
from pathlib import Path

import pytest

from app.ingestion.clone import CloneError, clone_repository


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def source_repo(tmp_path: Path) -> Path:
    src = tmp_path / "source"
    src.mkdir()
    (src / "hello.py").write_text("print('hello')\n")
    _git("init", "-q", cwd=src)
    _git("add", ".", cwd=src)
    _git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init", cwd=src)
    return src


def test_clones_repository(source_repo: Path, tmp_path: Path) -> None:
    dest = tmp_path / "repos" / "owner" / "name"
    result = clone_repository(source_repo.as_uri(), dest)
    assert result == dest
    assert (dest / "hello.py").read_text() == "print('hello')\n"
    assert not dest.with_name("name.partial").exists()


def test_reuses_existing_clone(source_repo: Path, tmp_path: Path) -> None:
    dest = tmp_path / "clone"
    clone_repository(source_repo.as_uri(), dest)
    (dest / "marker.txt").write_text("still here")
    clone_repository(source_repo.as_uri(), dest)
    assert (dest / "marker.txt").exists()


def test_failed_clone_leaves_nothing_behind(tmp_path: Path) -> None:
    dest = tmp_path / "clone"
    missing = (tmp_path / "does-not-exist").as_uri()
    with pytest.raises(CloneError, match="git clone failed"):
        clone_repository(missing, dest)
    assert not dest.exists()
    assert not dest.with_name("clone.partial").exists()


def test_refuses_non_git_destination(tmp_path: Path) -> None:
    dest = tmp_path / "clone"
    dest.mkdir()
    (dest / "file.txt").write_text("not a repo")
    with pytest.raises(CloneError, match="not a git repository"):
        clone_repository("unused", dest)
