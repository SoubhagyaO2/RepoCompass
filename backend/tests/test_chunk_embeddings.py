from collections.abc import Iterator
from pathlib import Path

import pytest
from fakes import FakeEmbedder

from app.ingestion.discovery import discover_files
from app.models.repository import IngestedRepository, RepoRef
from app.retrieval.chunk_embeddings import ast_collection_name, build_ast_embeddings
from app.retrieval.vector_store import VectorStore

AUTH = '''\
def authenticate_user(username, password):
    """Check credentials."""
    return verify_password(password)


class SessionStore:
    def save_session(self, data):
        return data
'''


@pytest.fixture
def store(tmp_path: Path) -> Iterator[VectorStore]:
    with VectorStore(tmp_path / "qdrant") as s:
        yield s


@pytest.fixture
def repo(tmp_path: Path) -> IngestedRepository:
    root = tmp_path / "repo"
    files = {
        "auth/service.py": AUTH,
        "db/engine.py": "def connect_database(url):\n    return create_engine(url)\n",
        "README.md": "not python",
    }
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return IngestedRepository(
        ref=RepoRef(owner="acme", name="widget"),
        local_path=root,
        files=discover_files(root),
    )


def test_collection_name_is_distinct_from_the_fixed_chunk_collection() -> None:
    assert ast_collection_name("pallets/flask") == "ast__pallets__flask"
    assert ast_collection_name("some-org/my_repo.js") == "ast__some-org__my_repo_js"


def test_search_returns_symbol_chunks_with_scores(
    repo: IngestedRepository, store: VectorStore
) -> None:
    retriever, stats = build_ast_embeddings(repo, FakeEmbedder(), store)

    assert (stats.files, stats.chunks) == (
        2,
        4,
    )  # authenticate_user, SessionStore, save_session, connect
    assert stats.total_seconds >= stats.embedding_seconds >= 0

    (top, *rest) = retriever.search("save session data", 4)
    assert (top.file_path, top.symbol_name, top.symbol_type) == (
        "auth/service.py",
        "SessionStore.save_session",
        "method",
    )
    assert (top.start_line, top.end_line) == (7, 8)
    assert top.score >= rest[0].score
    assert len(retriever.search("anything", 2)) == 2


def test_rebuilding_replaces_the_collection(repo: IngestedRepository, store: VectorStore) -> None:
    build_ast_embeddings(repo, FakeEmbedder(), store)
    build_ast_embeddings(repo, FakeEmbedder(), store)
    assert store.count(ast_collection_name("acme/widget")) == 4
