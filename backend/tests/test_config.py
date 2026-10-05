from pathlib import Path

import pytest

from app.config import Settings


def test_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.data_dir == Path("data")
    assert settings.repos_dir == Path("data") / "repos"


def test_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("REPOCOMPASS_DATA_DIR", str(tmp_path))
    settings = Settings(_env_file=None)
    assert settings.data_dir == tmp_path
