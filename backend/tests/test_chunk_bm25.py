from pathlib import Path

import pytest

from app.ingestion.discovery import discover_files
from app.models.repository import IngestedRepository, RepoRef
from app.retrieval.chunk_bm25 import build_ast_bm25, build_fixed_bm25

AUTH = '''\
def authenticate_user(username, password):
    """Check credentials."""
    return verify_password(password)


class SessionStore:
    def save_session(self, data):
        return data
'''
DB = "def connect_database(url):\n    return create_engine(url)\n"


@pytest.fixture
def repo(tmp_path: Path) -> IngestedRepository:
    files = {
        "auth/service.py": AUTH,
        "db/engine.py": DB,
        "tests/test_db.py": "def test_connect():\n    assert connect_database('x')\n",
        "README.md": "authenticate everything",
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    return IngestedRepository(
        ref=RepoRef(owner="acme", name="widget"),
        local_path=tmp_path,
        files=discover_files(tmp_path),
    )


def test_fixed_bm25_finds_the_file_by_identifier(repo: IngestedRepository) -> None:
    retriever, stats = build_fixed_bm25(repo, size=1000, overlap=200)
    assert (stats.files, stats.chunks) == (3, 3)  # README.md is not Python
    assert stats.total_seconds >= stats.chunking_seconds >= 0

    hits = retriever.search("where is authenticate_user defined?", 5)
    assert hits[0].file_path == "auth/service.py"
    assert hits[0].symbol_name is None  # fixed chunks know nothing about symbols
    assert hits[0].score > 0


def test_ast_bm25_returns_symbol_chunks(repo: IngestedRepository) -> None:
    retriever, stats = build_ast_bm25(repo)
    assert stats.files == 3
    assert (
        stats.chunks == 5
    )  # authenticate_user, SessionStore, save_session, connect_database, test

    (top, *_) = retriever.search("save_session", 5)
    assert (top.file_path, top.symbol_name, top.symbol_type) == (
        "auth/service.py",
        "SessionStore.save_session",
        "method",
    )
    assert (top.start_line, top.end_line) == (7, 8)
    assert top.category == "source"


def test_search_respects_depth_and_returns_only_matches(repo: IngestedRepository) -> None:
    retriever, _ = build_ast_bm25(repo)
    assert len(retriever.search("connect_database", 1)) == 1
    assert retriever.search("zzzz_no_such_word", 5) == []
    # the test-file chunk also mentions connect_database, so it is retrieved too
    files = [h.file_path for h in retriever.search("connect_database", 5)]
    assert set(files) == {"db/engine.py", "tests/test_db.py"}
