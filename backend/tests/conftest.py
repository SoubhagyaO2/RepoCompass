"""Tests must not depend on the developer's shell: a configured live index (the variables that
point the API at a repository) would make tests that expect "no index" fail."""

import pytest

from app.config import get_settings


@pytest.fixture(autouse=True)
def _no_ambient_live_index(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REPOCOMPASS_LIVE_REPOSITORY", raising=False)
    monkeypatch.delenv("REPOCOMPASS_LIVE_ROOT", raising=False)
    get_settings.cache_clear()
