from pathlib import Path

import pytest

from app.config import Settings
from app.ingestion import service
from app.ingestion.url import InvalidRepoURLError


def test_ingest_repository_clones_into_data_dir_and_summarises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cloned: list[tuple[str, Path]] = []

    def fake_clone(clone_url: str, dest: Path) -> Path:
        cloned.append((clone_url, dest))
        for rel in ["a.py", "pkg/b.py", "web/c.js", "README.md", "node_modules/x.js"]:
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            (dest / rel).write_text("x")
        return dest

    monkeypatch.setattr(service, "clone_repository", fake_clone)
    settings = Settings(_env_file=None, data_dir=tmp_path)

    repo = service.ingest_repository("https://github.com/acme/widget", settings)

    assert cloned == [("https://github.com/acme/widget.git", tmp_path / "repos/acme/widget")]
    summary = repo.summary()
    assert summary.repository == "acme/widget"
    assert summary.total_files == 4
    assert summary.files_by_language == {"Python": 2, "JavaScript": 1, "Other": 1}
    assert list(summary.files_by_language)[-1] == "Other"


def test_invalid_url_never_reaches_git(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args: object) -> None:
        raise AssertionError("clone should not be called")

    monkeypatch.setattr(service, "clone_repository", fail)
    with pytest.raises(InvalidRepoURLError):
        service.ingest_repository("https://github.com/--upload-pack=evil")
