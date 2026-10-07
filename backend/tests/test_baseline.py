"""Baseline tests use a fake embedder (hashed bag of words) so they need no model download."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fakes import FakeEmbedder

from app.ingestion.discovery import discover_files
from app.models.repository import IngestedRepository, RepoRef
from app.retrieval.baseline import FixedChunkBaseline, collection_name
from app.retrieval.vector_store import VectorStore


@pytest.fixture
def store(tmp_path: Path) -> Iterator[VectorStore]:
    with VectorStore(tmp_path / "qdrant") as s:
        yield s


def make_repo(tmp_path: Path) -> IngestedRepository:
    files = {
        "auth/service.py": (
            "def authenticate_user(username, password):\n    return verify_password(password)\n"
        ),
        "db/engine.py": "def connect_database(url):\n    return create_engine(url)\n",
        "tests/test_db.py": "def test_connect():\n    assert connect_database('x')\n",
        "README.md": "authenticate everything",
    }
    root = tmp_path / "repo"
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return IngestedRepository(
        ref=RepoRef(owner="acme", name="widget"), local_path=root, files=discover_files(root)
    )


def test_index_then_search_returns_the_relevant_file_first(
    tmp_path: Path, store: VectorStore
) -> None:
    baseline = FixedChunkBaseline(FakeEmbedder(), store)
    stats = baseline.index(make_repo(tmp_path))

    assert stats.files == 3  # README.md is not Python
    assert stats.chunks == 3
    assert stats.embedding_model == "fake-bow"
    assert store.count(collection_name("acme/widget")) == 3
    assert baseline.is_indexed("acme/widget")
    assert not baseline.is_indexed("acme/other")

    hits = baseline.search("acme/widget", "authenticate_user password", k=2)
    assert len(hits) == 2
    assert hits[0].file_path == "auth/service.py"
    assert (hits[0].start_line, hits[0].end_line) == (1, 2)
    assert hits[0].category == "source"
    assert hits[0].score >= hits[1].score


def test_reindexing_replaces_instead_of_duplicating(tmp_path: Path, store: VectorStore) -> None:
    baseline = FixedChunkBaseline(FakeEmbedder(), store)
    repo = make_repo(tmp_path)
    baseline.index(repo)
    baseline.index(repo)
    assert store.count(collection_name("acme/widget")) == 3


def test_collection_name_is_qdrant_safe() -> None:
    assert collection_name("pallets/flask") == "fixed__pallets__flask"
    assert collection_name("some-org/my_repo.js") == "fixed__some-org__my_repo_js"
