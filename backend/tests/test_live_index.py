"""The live index: incremental updates must give exactly the index a full rebuild would."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from app.indexing.live_index import IndexStateError, LiveIndex
from app.retrieval.embedder import HashingEmbedder
from app.retrieval.vector_store import VectorStore

REPO = "acme/widget"
PROBES = [
    "where is alpha_func2 defined",
    "session cookie request handler",
    "beta_func0",
    "Greeter greet",
    "ensure_sync",
    "what calls helper_func1",
    "nothing matches zzzqqq",
]


class CountingEmbedder(HashingEmbedder):
    """Counts how many texts reach the 'model'."""

    def __init__(self) -> None:
        self.texts_embedded = 0

    def embed_documents(self, texts: list[str], show_progress: bool = False) -> list[list[float]]:
        self.texts_embedded += len(texts)
        return super().embed_documents(texts, show_progress)


def module(name: str, n_funcs: int = 6, extra: str = "") -> str:
    lines = [f'"""Module {name}."""', ""]
    for i in range(n_funcs):
        lines += [
            f"def {name}_func{i}(x):",
            f'    """Compute {name} thing number {i} for the request."""',
            f"    value = x * {i + 1} + {i}",
            "    return value  # " + "pad " * 40,
            "",
        ]
    return "\n".join(lines) + "\n" + extra


BASE_FILES = {
    "pkg/alpha.py": module("alpha"),
    "pkg/beta.py": module("beta", extra="\nfrom pkg.alpha import alpha_func1\n"),
    "pkg/helper.py": module("helper")
    + "class Greeter:\n    def greet(self):\n        return alpha_func0(1)\n",
    "pkg/app.py": (
        "from pkg.helper import Greeter\n\n"
        "class App:\n    def ensure_sync(self, f):\n        return f\n\n"
        "    def run(self):\n        return Greeter().greet()\n"
    ),
    "pkg/user.py": "def use(app):\n    return app.ensure_sync(1)\n",
    "tests/test_alpha.py": "def test_it():\n    assert True\n",
}


def write(root: Path, rel: str, text: str) -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_bytes(text.encode())  # bytes: no CRLF translation on Windows


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "work"
    for rel, text in BASE_FILES.items():
        write(root, rel, text)
    return root


@pytest.fixture
def stores(tmp_path: Path) -> Iterator[tuple[VectorStore, VectorStore]]:
    with VectorStore(tmp_path / "q1") as a, VectorStore(tmp_path / "q2") as b:
        yield a, b


def make_index(
    root: Path, state: Path, store: VectorStore, embedder: HashingEmbedder | None = None
) -> LiveIndex:
    return LiveIndex(REPO, root, state, store, embedder or HashingEmbedder(), 600, 120)


def assert_equals_full_rebuild(
    index: LiveIndex, tmp_path: Path, store: VectorStore, tag: str
) -> None:
    """The correctness criterion: the incremental index equals a full rebuild of the same files."""
    reference = make_index(index.root, tmp_path / f"ref-{tag}", store)
    reference.build_full()
    assert index.snapshot(PROBES) == reference.snapshot(PROBES)  # exact, BM25 scores included
    for view in ("fixed", "ast"):
        assert_vectors_close(index.vectors(view), reference.vectors(view))


def assert_vectors_close(a: dict[str, list[float]], b: dict[str, list[float]]) -> None:
    """Same chunks, vectors equal up to float32 rounding. Qdrant normalises vectors on insert, so
    a reused vector read back and re-inserted is normalised twice; the drift is a few ulps."""
    assert a.keys() == b.keys()
    worst = max((abs(x - y) for k in a for x, y in zip(a[k], b[k], strict=True)), default=0.0)
    assert worst < 1e-6, worst


# --- building and persistence -------------------------------------------------------------


def test_full_build_then_noop_update_does_nothing(tree: Path, stores, tmp_path: Path) -> None:
    embedder = CountingEmbedder()
    index = make_index(tree, tmp_path / "state", stores[0], embedder)
    first = index.build_full()
    assert (first.added, first.modified, first.deleted) == (len(BASE_FILES), 0, 0)
    assert all(v.embedded_chunks == v.new_chunks > 0 for v in first.views.values())

    before = embedder.texts_embedded
    second = index.update()
    assert (second.added, second.modified, second.deleted) == (0, 0, 0)
    assert second.unchanged == len(BASE_FILES) and embedder.texts_embedded == before


def test_touching_a_file_without_changing_its_bytes_costs_nothing(
    tree: Path, stores, tmp_path: Path
) -> None:
    embedder = CountingEmbedder()
    index = make_index(tree, tmp_path / "state", stores[0], embedder)
    index.build_full()
    before = embedder.texts_embedded
    write(tree, "pkg/alpha.py", BASE_FILES["pkg/alpha.py"])  # rewritten: new mtime, same bytes
    stats = index.update()
    assert stats.modified == 0 and embedder.texts_embedded == before


def test_a_new_process_can_load_the_stored_index_and_keep_updating(
    tree: Path, stores, tmp_path: Path
) -> None:
    first = make_index(tree, tmp_path / "state", stores[0])
    first.build_full()
    snapshot = first.snapshot(PROBES)

    second = make_index(tree, tmp_path / "state", stores[0])  # fresh object, same disk state
    assert second.load() is True
    assert second.snapshot(PROBES) == snapshot

    write(tree, "pkg/alpha.py", module("alpha") + "# edited\n")
    assert second.update().modified == 1
    assert_equals_full_rebuild(second, tmp_path, stores[1], "reload")


def test_loading_refuses_a_different_configuration_and_a_missing_collection(
    tree: Path, stores, tmp_path: Path
) -> None:
    make_index(tree, tmp_path / "state", stores[0]).build_full()
    other = LiveIndex(REPO, tree, tmp_path / "state", stores[0], HashingEmbedder(), 900, 120)
    with pytest.raises(IndexStateError, match="rebuild"):
        other.load()

    stores[0].drop_collection("live_ast__acme_widget")
    with pytest.raises(IndexStateError, match="missing"):
        make_index(tree, tmp_path / "state", stores[0]).load()
    assert make_index(tree, tmp_path / "nowhere", stores[0]).load() is False


# --- incremental == full rebuild, scenario by scenario ------------------------------------------


def test_editing_one_function_reembeds_only_that_function(
    tree: Path, stores, tmp_path: Path
) -> None:
    embedder = CountingEmbedder()
    index = make_index(tree, tmp_path / "state", stores[0], embedder)
    index.build_full()
    before = embedder.texts_embedded

    edited = BASE_FILES["pkg/alpha.py"].replace("value = x * 3 + 2", "value = x * 3 + 99")
    write(tree, "pkg/alpha.py", edited)
    stats = index.update()

    assert (stats.added, stats.modified, stats.deleted) == (0, 1, 0)
    assert stats.views["ast"].model_texts == 1  # just the one changed function
    assert stats.views["ast"].reused_chunks == stats.views["ast"].new_chunks - 1
    assert stats.views["ast"].model_texts < stats.views["fixed"].new_chunks
    assert (
        embedder.texts_embedded - before
        == stats.views["ast"].model_texts + stats.views["fixed"].model_texts
    )
    assert_equals_full_rebuild(index, tmp_path, stores[1], "edit")


def test_inserting_at_the_top_shifts_every_fixed_window_but_not_the_ast_chunks(
    tree: Path, stores, tmp_path: Path
) -> None:
    index = make_index(tree, tmp_path / "state", stores[0])
    index.build_full()
    write(tree, "pkg/alpha.py", "# a new first line\n" + BASE_FILES["pkg/alpha.py"])
    stats = index.update()

    fixed, ast = stats.views["fixed"], stats.views["ast"]
    assert fixed.model_texts >= fixed.new_chunks - 1  # windows moved: nearly all texts are new
    assert ast.model_texts <= 1  # AST chunks keep their text (line numbers change, vectors reused)
    assert ast.points_upserted == ast.new_chunks  # but every point's payload (lines) is rewritten
    assert_equals_full_rebuild(index, tmp_path, stores[1], "top")


def test_adding_a_file(tree: Path, stores, tmp_path: Path) -> None:
    index = make_index(tree, tmp_path / "state", stores[0])
    index.build_full()
    write(tree, "pkg/gamma.py", module("gamma", n_funcs=3))
    stats = index.update()
    assert (stats.added, stats.modified, stats.deleted) == (1, 0, 0)
    assert_equals_full_rebuild(index, tmp_path, stores[1], "add")


def test_deleting_a_file_removes_its_points_chunks_and_graph_edges(
    tree: Path, stores, tmp_path: Path
) -> None:
    index = make_index(tree, tmp_path / "state", stores[0])
    index.build_full()
    assert index.graph is not None and index.graph.has_edge(
        "imports", "pkg/app.py", "pkg/helper.py"
    )
    (tree / "pkg" / "helper.py").unlink()
    stats = index.update()

    assert (stats.deleted, stats.modified) == (1, 0)
    assert stats.views["fixed"].points_deleted > 0 and stats.views["fixed"].new_chunks == 0
    assert not index.graph.has_edge("imports", "pkg/app.py", "pkg/helper.py")
    assert stats.graph_edges_removed > 0
    assert_equals_full_rebuild(index, tmp_path, stores[1], "delete")


def test_renaming_a_file_reuses_every_vector(tree: Path, stores, tmp_path: Path) -> None:
    embedder = CountingEmbedder()
    index = make_index(tree, tmp_path / "state", stores[0], embedder)
    index.build_full()
    before = embedder.texts_embedded

    text = (tree / "pkg" / "beta.py").read_bytes()
    (tree / "pkg" / "beta.py").unlink()
    (tree / "pkg" / "beta_renamed.py").write_bytes(text)
    stats = index.update()

    assert (stats.added, stats.deleted) == (1, 1)
    assert embedder.texts_embedded == before  # nothing was embedded: the text is unchanged
    assert all(v.model_texts == 0 for v in stats.views.values())
    assert_equals_full_rebuild(index, tmp_path, stores[1], "rename")


def test_five_files_changed_at_once(tree: Path, stores, tmp_path: Path) -> None:
    index = make_index(tree, tmp_path / "state", stores[0])
    index.build_full()
    for rel in ("pkg/alpha.py", "pkg/beta.py", "pkg/helper.py", "pkg/app.py", "pkg/user.py"):
        write(tree, rel, BASE_FILES[rel] + "# edited\n")
    stats = index.update()
    assert stats.modified == 5
    assert_equals_full_rebuild(index, tmp_path, stores[1], "five")


def test_a_change_in_one_file_can_change_the_graph_of_files_that_did_not_change(
    tree: Path, stores, tmp_path: Path
) -> None:
    index = make_index(tree, tmp_path / "state", stores[0])
    index.build_full()
    assert index.graph is not None
    edge = ("pkg/user.py::use", "pkg/app.py::App.ensure_sync")
    assert index.graph.has_edge("calls", *edge)  # resolved: ensure_sync is a unique method name

    # 1. a second class defines the same method name: the call in the unchanged user.py becomes
    #    ambiguous and its edge must disappear
    write(tree, "pkg/other.py", "class Other:\n    def ensure_sync(self):\n        return 1\n")
    index.update()
    assert not index.graph.has_edge("calls", *edge)
    assert_equals_full_rebuild(index, tmp_path, stores[1], "ambiguity")

    # 2. remove it again: the edge comes back, with no change to user.py or app.py
    (tree / "pkg" / "other.py").unlink()
    stats = index.update()
    assert index.graph.has_edge("calls", *edge) and stats.graph_edges_added >= 1
    assert_equals_full_rebuild(index, tmp_path, stores[1], "ambiguity-undone")

    # 3. rename the method: edges from files that did not change disappear
    write(
        tree, "pkg/app.py", BASE_FILES["pkg/app.py"].replace("def ensure_sync", "def ensure_sync2")
    )
    index.update()
    assert not index.graph.has_edge("calls", *edge)
    assert_equals_full_rebuild(index, tmp_path, stores[1], "rename-symbol")


def test_a_sequence_of_edits_and_restores_ends_where_it_started(
    tree: Path, stores, tmp_path: Path
) -> None:
    index = make_index(tree, tmp_path / "state", stores[0])
    index.build_full()
    start = index.snapshot(PROBES)

    for _ in range(2):
        write(tree, "pkg/alpha.py", "# x\n" + BASE_FILES["pkg/alpha.py"])
        write(tree, "pkg/new.py", module("new", 2))
        (tree / "pkg" / "user.py").unlink()
        index.update()
        write(tree, "pkg/alpha.py", BASE_FILES["pkg/alpha.py"])
        (tree / "pkg" / "new.py").unlink()
        write(tree, "pkg/user.py", BASE_FILES["pkg/user.py"])
        index.update()
    assert index.snapshot(PROBES) == start


def test_an_interrupted_update_is_repaired_by_the_next_one(
    tree: Path, stores, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = make_index(tree, tmp_path / "state", stores[0])
    index.build_full()
    write(tree, "pkg/alpha.py", module("alpha") + "# crash test\n")

    def crash() -> None:
        raise RuntimeError("power cut before the manifest was written")

    monkeypatch.setattr(index, "_write_manifest", crash)
    with pytest.raises(RuntimeError, match="power cut"):
        index.update()
    monkeypatch.undo()

    # a new process starts from what is on disk: the old manifest, new points already written
    recovered = make_index(tree, tmp_path / "state", stores[0])
    assert recovered.load() is True
    recovered.update()
    assert_equals_full_rebuild(recovered, tmp_path, stores[1], "crash")


# --- searching ----------------------------------------------------------------------------------


def test_live_bm25_search_returns_the_same_hits_as_the_static_pipeline(
    tree: Path, stores, tmp_path: Path
) -> None:
    from app.ingestion.discovery import discover_files
    from app.models.repository import IngestedRepository, RepoRef
    from app.retrieval.chunk_bm25 import build_ast_bm25, build_fixed_bm25

    index = make_index(tree, tmp_path / "state", stores[0])
    index.build_full()
    ingested = IngestedRepository(
        ref=RepoRef(owner="acme", name="widget"), local_path=tree, files=discover_files(tree)
    )
    static = {"fixed": build_fixed_bm25(ingested, 600, 120)[0], "ast": build_ast_bm25(ingested)[0]}

    for view in ("fixed", "ast"):
        for query in PROBES:
            live_hits = index.bm25_search(view)(query, 50)
            static_hits = static[view].search(query, 50)
            key = lambda h: (h.file_path, h.start_line, h.end_line, h.score, h.symbol_name)  # noqa: E731
            assert [key(h) for h in live_hits] == [key(h) for h in static_hits]


def test_search_hits_carry_structured_evidence(tree: Path, stores, tmp_path: Path) -> None:
    index = make_index(tree, tmp_path / "state", stores[0])
    index.build_full()
    (hit, *_) = index.bm25_search("ast")("alpha_func2", 5)
    assert hit.file_path == "pkg/alpha.py" and hit.symbol_name == "alpha_func2"
    assert hit.symbol_type == "function" and hit.start_line < hit.end_line and hit.score > 0
    assert hit.category == "source"

    hybrid = index.hybrid("ast")("alpha_func2 compute thing", 20)
    assert hybrid and len({h.file_path for h in hybrid}) == len(hybrid)  # one hit per file


# --- repair on load -----------------------------------------------------------------------------


def test_loading_removes_stray_points_and_reports_missing_ones(
    tree: Path, stores, tmp_path: Path
) -> None:
    from app.indexing.live_index import point_id

    index = make_index(tree, tmp_path / "state", stores[0])
    index.build_full()
    collection = index.collection("ast")

    stray = point_id("acme/widget:ghost.py:1")
    stores[0].upsert_points(collection, [stray], [[1.0] + [0.0] * 255], [{"id": "ghost"}])
    assert stray in stores[0].point_ids(collection)
    assert make_index(tree, tmp_path / "state", stores[0]).load() is True
    assert stray not in stores[0].point_ids(collection)  # an interrupted update's leftover

    victim = next(iter(stores[0].point_ids(collection)))
    stores[0].delete_points(collection, [victim])
    with pytest.raises(IndexStateError, match="missing"):
        make_index(tree, tmp_path / "state", stores[0]).load()
