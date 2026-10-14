"""Cloning on behalf of a web request (Milestone: connect a repository): what git is allowed to
do, what it can see, and how much it may fetch."""

import subprocess
from pathlib import Path
from typing import Any

import pytest

from app.ingestion import clone as clone_module
from app.ingestion.clone import CloneError, clone_repository, directory_size_mb


class FakeRun:
    def __init__(self) -> None:
        self.cmd: list[str] = []
        self.env: dict[str, str] = {}

    def __call__(self, cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.cmd, self.env = cmd, kwargs["env"]
        Path(cmd[-1]).mkdir(parents=True)  # the temporary clone directory
        (Path(cmd[-1]) / ".git").mkdir()
        return subprocess.CompletedProcess(cmd, 0, "", "")


def test_hardened_clone_allows_only_https_and_no_redirects_hooks_or_symlinks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeRun()
    monkeypatch.setattr(clone_module.subprocess, "run", fake)
    clone_repository("https://github.com/o/r.git", tmp_path / "o" / "r", hardened=True)
    flags = " ".join(fake.cmd)
    assert "protocol.allow=never" in flags and "protocol.https.allow=always" in flags
    assert "http.followRedirects=false" in flags and "core.symlinks=false" in flags
    assert "core.hooksPath=" in flags
    assert "--depth" in fake.cmd and "--no-tags" in fake.cmd
    assert "--" in fake.cmd and fake.cmd[fake.cmd.index("--") + 1] == "https://github.com/o/r.git"


def test_hardened_clone_does_not_pass_secrets_to_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "SECRET-VALUE")
    monkeypatch.setenv("REPOCOMPASS_GEMINI_API_KEY", "SECRET-2")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "SECRET-3")
    fake = FakeRun()
    monkeypatch.setattr(clone_module.subprocess, "run", fake)
    clone_repository("https://github.com/o/r.git", tmp_path / "o" / "r", hardened=True)
    assert not any("SECRET" in value for value in fake.env.values())
    assert fake.env["GIT_TERMINAL_PROMPT"] == "0" and "PATH" in fake.env


def test_the_default_clone_is_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeRun()
    monkeypatch.setattr(clone_module.subprocess, "run", fake)
    clone_repository("file:///somewhere", tmp_path / "x", hardened=False)
    assert "protocol.allow=never" not in " ".join(fake.cmd)


def test_error_text_from_git_is_returned_without_the_local_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing(cmd: list[str], **kwargs: Any) -> None:
        raise subprocess.CalledProcessError(128, cmd, stderr=f"fatal: could not read {cmd[-1]}")

    monkeypatch.setattr(clone_module.subprocess, "run", failing)
    with pytest.raises(CloneError) as raised:
        clone_repository("https://github.com/o/r.git", tmp_path / "o" / "r", hardened=True)
    assert str(tmp_path) not in str(raised.value)


def test_directory_size_counts_files_and_ignores_the_git_directory(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_bytes(b"x" * 1_048_576)
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "pack").write_bytes(b"y" * 5_000_000)
    assert directory_size_mb(tmp_path) == pytest.approx(1.0, abs=0.01)
