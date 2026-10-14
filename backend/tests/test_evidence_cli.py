"""The evidence command line refuses a root that cannot hold an index (a mistyped path used to
build an empty index and report success)."""

from pathlib import Path

import pytest

from app.evidence.__main__ import main, root_problem


def test_a_missing_directory_is_a_problem(tmp_path: Path) -> None:
    assert "does not exist" in (root_problem(tmp_path / "nope") or "")


def test_a_directory_without_python_files_is_a_problem(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("hi", encoding="utf-8")
    assert "no Python files" in (root_problem(tmp_path) or "")


def test_a_directory_with_python_files_is_fine(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    assert root_problem(tmp_path) is None


def test_build_with_a_bad_root_exits_2_before_opening_the_index(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.evidence.__main__ as cli

    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("the index must not be opened")

    monkeypatch.setattr(cli, "open_live_index", boom)
    code = main(["--repository", "x", "--root", str(tmp_path / "missing"), "--build"])
    assert code == 2 and "does not exist" in capsys.readouterr().err
