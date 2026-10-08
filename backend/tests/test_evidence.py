"""Evidence API tests (Milestone 12): schema, symbol mapping, graph annotations, determinism,
edge cases from the plan's table, and consistency with incremental updates."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.evidence.builder import (
    DEPTH,
    MAX_RELATIONSHIPS,
    EvidenceBuilder,
    EvidenceQueryError,
)
from app.evidence.schema import EvidenceResponse
from app.evidence.validate import check_response, compare_evidence
from app.indexing.live_index import LiveIndex
from app.main import create_app
from app.retrieval.embedder import HashingEmbedder, SeededEmbedder
from app.retrieval.hybrid import HybridRetriever
from app.retrieval.vector_store import VectorStore

REPO = "synthetic"

CORE = '''"""Core engine."""
from typing import overload

LIMIT = 10
NAME = "engine"


class Engine:
    """Runs jobs."""

    mode = "fast"

    def run(self):
        return helper(LIMIT)

    def stop(self):
        return None


@overload
def convert(value: int) -> int: ...


@overload
def convert(value: str) -> str: ...


def convert(value):
    def inner(v):
        return v

    return inner(value)


def helper(n):
    return n


def start():
    engine = Engine()
    engine.run()
    return engine
'''

OTHER = """class Other:
    def stop(self):
        return 1


def shutdown(thing):
    thing.stop()
"""

BROKEN = "def broken(:\n    pass\n\ndef fine():\n    return 1\n"

TEST_CORE = """from core import start


def test_start():
    assert start()
"""

HUB = "def hub():\n    return 1\n"
CALLERS = "from hubdef import hub\n\n\n" + "".join(
    f"def caller_{i}():\n    return hub()\n\n\n" for i in range(12)
)
KLASS = '''class Zebrafish:
    """Zebrafish docs."""

    stripes = 3

    def swim(self):
        return 1
'''

FILES = {
    "core.py": CORE,
    "other.py": OTHER,
    "broken.py": BROKEN,
    "hubdef.py": HUB,
    "callers.py": CALLERS,
    "klass.py": KLASS,
    "tests/test_core.py": TEST_CORE,
}


def write(root: Path, rel: str, text: str) -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_bytes(text.encode())


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "work"
    for rel, text in FILES.items():
        write(root, rel, text)
    return root


@pytest.fixture
def store(tmp_path: Path) -> Iterator[VectorStore]:
    with VectorStore(tmp_path / "q") as s:
        yield s


def make_index(root: Path, state: Path, store: VectorStore, embedder: Any = None) -> LiveIndex:
    return LiveIndex(REPO, root, state, store, embedder or HashingEmbedder(), 600, 120)


@pytest.fixture
def builder(root: Path, tmp_path: Path, store: VectorStore) -> EvidenceBuilder:
    index = make_index(root, tmp_path / "state", store)
    index.build_full()
    return EvidenceBuilder(index, revision="abc123")


QUERIES = [
    "engine run helper", "convert value", "hub caller", "hub", "stop", "start engine", "LIMIT",
    "zebrafish stripes",
]  # fmt: skip


def all_items(builder: EvidenceBuilder) -> list[Any]:
    return [
        item for q in QUERIES for item in builder.build(q, top_k=DEPTH, include_text=True).evidence
    ]


# --- invariants ---------------------------------------------------------------------------------


@pytest.mark.parametrize("query", QUERIES)
def test_every_invariant_holds(builder: EvidenceBuilder, root: Path, query: str) -> None:
    response = builder.build(query, top_k=DEPTH, include_text=True)
    assert response.evidence
    assert check_response(response, builder.index, root) == []


def test_response_metadata(builder: EvidenceBuilder) -> None:
    r = builder.build("engine")
    assert r.schema_version == 1
    assert r.index.repository == REPO and r.index.revision == "abc123" and r.index.view == "ast"
    assert r.index.files == len(FILES)
    assert r.retrieval.rrf_k == 60 and r.retrieval.components == ["bm25", "embedding"]
    assert "not evidence" in r.notes["graph"] and "not comparable" in r.notes["scores"]


def test_evidence_is_deterministic(builder: EvidenceBuilder) -> None:
    for q in QUERIES:
        assert compare_evidence(builder.build(q), builder.build(q)) == []


def test_evidence_is_exactly_the_hybrid_ranking(builder: EvidenceBuilder) -> None:
    """Evidence is a view: same files, same order, same scores as the plain retriever."""
    retriever = HybridRetriever(
        [builder.index.bm25_search("ast"), builder.index.embedding_search("ast")]
    )
    for q in QUERIES:
        plain = retriever.search(q, DEPTH)
        evidence = builder.build(q, top_k=DEPTH).evidence
        assert [i.file for i in evidence] == [h.file_path for h in plain]
        assert [i.scores.rrf for i in evidence] == pytest.approx([h.score for h in plain], abs=1e-6)
        assert [(i.line_start, i.line_end) for i in evidence] == [
            (h.start_line, h.end_line) for h in plain
        ]


def test_top_k_only_truncates(builder: EvidenceBuilder) -> None:
    full = builder.build("engine run", top_k=DEPTH).evidence
    short = builder.build("engine run", top_k=2).evidence
    assert [i.file for i in short] == [i.file for i in full[:2]]
    assert len(builder.build("engine run", top_k=DEPTH).evidence) <= len(FILES)


def test_text_is_off_by_default(builder: EvidenceBuilder) -> None:
    assert all(i.text is None for i in builder.build("engine").evidence)
    assert all(i.text for i in builder.build("engine", include_text=True).evidence)


# --- edge cases from the plan -------------------------------------------------------------------


def test_module_level_chunk_has_no_symbol(builder: EvidenceBuilder) -> None:
    items = [i for i in all_items(builder) if i.symbol_status == "module_level"]
    assert items
    assert all(i.symbol is None for i in items)
    assert all(i.graph is not None and i.graph.node == i.file for i in items)


def test_class_header_reports_the_class_extent(builder: EvidenceBuilder) -> None:
    headers = [i for i in all_items(builder) if i.symbol and i.symbol.kind == "class"]
    fish = next(i for i in headers if i.symbol and i.symbol.qualified_name == "Zebrafish")
    assert fish.symbol is not None
    assert fish.line_end < fish.symbol.line_end  # the header is shorter than the class
    assert fish.line_start == fish.symbol.line_start
    assert fish.symbol_status == "exact"


def test_overloads_are_told_apart_by_start_line(builder: EvidenceBuilder) -> None:
    record = builder.index.records["core.py"]
    converts = [c for c in record.chunks["ast"] if c.symbol_name == "convert"]
    assert len(converts) == 3  # two overload stubs and the implementation
    symbols = [builder._symbol(record, c) for c in converts]
    assert [s.line_start for s in symbols if s] == [c.start_line for c in converts]
    assert len({s.node_id for s in symbols if s}) == 1  # one shared graph node


def test_nested_function_is_not_a_separate_item(builder: EvidenceBuilder) -> None:
    record = builder.index.records["core.py"]
    names = {builder._symbol(record, c).qualified_name for c in record.chunks["ast"]
             if builder._symbol(record, c)}  # fmt: skip
    assert "convert" in names
    assert not any(n.endswith("inner") for n in names if n != "convert")  # inside convert's chunk


def test_unparsed_when_the_parse_has_no_such_symbol(builder: EvidenceBuilder) -> None:
    record = builder.index.records["core.py"]
    record.parsed = record.parsed.model_copy(update={"symbols": []})
    items = [
        i for i in builder.build("engine run helper", top_k=DEPTH).evidence if i.file == "core.py"
    ]
    assert items
    assert all(i.symbol is None for i in items)
    assert {i.symbol_status for i in items} <= {"unparsed", "module_level"}


def test_file_with_a_syntax_error_is_still_citable(builder: EvidenceBuilder, root: Path) -> None:
    r = builder.build("broken fine", top_k=DEPTH, include_text=True)
    assert any(i.file == "broken.py" for i in r.evidence)
    assert check_response(r, builder.index, root) == []


def test_ambiguous_method_edge_is_absent_not_invented(builder: EvidenceBuilder) -> None:
    """`thing.stop()` could be Engine.stop or Other.stop: the graph drops it, so does evidence."""
    r = builder.build("stop", top_k=DEPTH)
    for item in r.evidence:
        assert item.graph is not None
        for rel in item.graph.relationships:
            assert not (rel.other == "other.py::shutdown" and rel.relationship == "called_by")


def test_symbol_without_edges_has_empty_graph_not_none(builder: EvidenceBuilder) -> None:
    items = [i for i in all_items(builder) if i.symbol and i.symbol.qualified_name == "Engine.stop"]
    assert items
    g = items[0].graph
    assert g is not None and g.relationships == [] and g.degree.called_by == 0 and not g.truncated


def test_relationships_are_capped_but_degree_is_complete(builder: EvidenceBuilder) -> None:
    items = [i for i in all_items(builder) if i.symbol and i.symbol.qualified_name == "hub"]
    g = items[0].graph
    assert g is not None
    assert g.degree.called_by == 12
    assert len(g.relationships) == MAX_RELATIONSHIPS and g.truncated
    assert all(r.relationship == "called_by" for r in g.relationships)


def test_links_connect_items_of_the_same_response(builder: EvidenceBuilder) -> None:
    r = builder.build("hub caller", top_k=DEPTH)
    ranks = {i.graph.node: i.rank for i in r.evidence if i.graph}
    hub = next(i for i in r.evidence if i.graph and i.graph.node == "hubdef.py::hub")
    assert hub.graph is not None
    assert hub.graph.links_to_other_results  # the retrieved caller is linked, even beyond the cap
    for link in hub.graph.links_to_other_results:
        assert link.other_rank in ranks.values() and link.other_rank != hub.rank
        assert link.relationship == "called_by"


def test_empty_index_returns_no_evidence(tmp_path: Path, store: VectorStore) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    index = make_index(empty, tmp_path / "state-empty", store)
    index.build_full()
    r = EvidenceBuilder(index).build("anything at all")
    assert r.evidence == []


@pytest.mark.parametrize(
    "query,top_k", [("", 10), ("   \n", 10), ("x" * 2001, 10), ("a", 0), ("a", DEPTH + 1)]
)
def test_invalid_requests_are_rejected(builder: EvidenceBuilder, query: str, top_k: int) -> None:
    with pytest.raises(EvidenceQueryError):
        builder.build(query, top_k)


def test_unicode_query_is_accepted(builder: EvidenceBuilder) -> None:
    assert builder.build("запуск движка ✓").evidence


def test_inconsistent_index_is_an_error_not_an_invented_item(builder: EvidenceBuilder) -> None:
    from app.evidence.builder import EvidenceError

    del builder.index.records["core.py"]
    with pytest.raises(EvidenceError):
        builder.build("engine run helper", top_k=DEPTH)


# --- consistency with incremental updates ---------------------------------------------------------


def test_evidence_after_an_update_equals_a_fresh_build(
    root: Path, tmp_path: Path, store: VectorStore
) -> None:
    """Uses SeededEmbedder: exact embedding ties are ordered by the vector store's insertion
    order, which an incremental update does not reproduce (documented deviation 2 of the plan)."""
    live = make_index(root, tmp_path / "state-live", store, SeededEmbedder())
    live.build_full()
    builder = EvidenceBuilder(live, revision="abc123")
    write(root, "core.py", CORE.replace("def helper(n):", "def helper2(n):"))  # rename a symbol
    write(root, "extra.py", "def extra():\n    return 1\n")
    builder.index.update()
    with VectorStore(tmp_path / "q-fresh") as fresh_store:
        fresh = make_index(root, tmp_path / "state-fresh", fresh_store, SeededEmbedder())
        fresh.build_full()
        fresh_builder = EvidenceBuilder(fresh, revision="abc123")
        for q in QUERIES + ["helper2", "extra"]:
            mine = builder.build(q, top_k=DEPTH, include_text=True)
            theirs = fresh_builder.build(q, top_k=DEPTH, include_text=True)
            scores = [i.scores.embedding.score for i in mine.evidence if i.scores.embedding]
            assert len(set(scores)) == len(scores), "precondition: no embedding ties"
            assert check_response(mine, builder.index, root) == []
            assert compare_evidence(mine, theirs) == []
            assert mine.index == theirs.index
    # `Engine.run` still calls `helper(...)`, which no longer exists: the renamed function has no
    # caller, although the calling code did not change (a cross-file-style graph change)
    r = builder.build("helper2", top_k=DEPTH)
    renamed = next(i for i in r.evidence if i.symbol and i.symbol.qualified_name == "helper2")
    assert renamed.graph is not None and renamed.graph.degree.called_by == 0


# --- HTTP ---------------------------------------------------------------------------------------


def test_http_evidence_and_status(builder: EvidenceBuilder) -> None:
    with TestClient(create_app(builder)) as client:
        r = client.get("/v1/evidence", params={"q": "engine run", "top_k": 3})
        assert r.status_code == 200
        body = r.json()
        assert len(body["evidence"]) == 3 and body["timing_ms"] is not None
        assert EvidenceResponse.model_validate(body).evidence[0].text is None
        with_text = client.get("/v1/evidence", params={"q": "engine", "include_text": "true"})
        assert with_text.json()["evidence"][0]["text"]
        status = client.get("/v1/index/status").json()
        assert status["repository"] == REPO and status["files"] == len(FILES)


@pytest.mark.parametrize(
    "params",
    [
        {"q": ""},
        {"q": "   "},
        {"q": "x" * 2001},
        {"q": "a", "top_k": 0},
        {"q": "a", "top_k": 51},
        {},
    ],
)
def test_http_rejects_bad_requests_with_422(
    builder: EvidenceBuilder, params: dict[str, Any]
) -> None:
    with TestClient(create_app(builder)) as client:
        assert client.get("/v1/evidence", params=params).status_code == 422


def test_http_without_an_index_is_503() -> None:
    with TestClient(create_app()) as client:
        assert client.get("/v1/evidence", params={"q": "x"}).status_code == 503
        assert client.get("/v1/index/status").status_code == 503
        assert client.get("/health").status_code == 200
