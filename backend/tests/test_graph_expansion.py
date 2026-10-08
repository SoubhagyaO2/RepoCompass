import textwrap

import pytest

from app.graph.builder import build_graph_from_parsed
from app.models.search import SearchHit
from app.parsing.python_parser import parse_python_source
from app.retrieval.graph_expansion import ANCHORS, GraphExpandedRetriever
from app.retrieval.hybrid import RRF_K


def graph_of(files: dict[str, str]):  # type: ignore[no-untyped-def]
    return build_graph_from_parsed(
        {p: parse_python_source(textwrap.dedent(c).encode(), p) for p, c in files.items()}
    )


def hit(path: str, score: float) -> SearchHit:
    return SearchHit(
        file_path=path, start_line=3, end_line=9, score=score, content=f"chunk of {path}",
        category="source",
    )  # fmt: skip


def first_stage(hits: list[SearchHit], seen: list[int] | None = None):  # type: ignore[no-untyped-def]
    def search(query: str, depth: int) -> list[SearchHit]:
        if seen is not None:
            seen.append(depth)
        return hits

    return search


GRAPH = graph_of(
    {
        # a (the top hit) imports b and is imported by c; d is unrelated to everything
        "a.py": "import b\n",
        "b.py": "x = 1\n",
        "c.py": "import a\n",
        "d.py": "y = 2\n",
    }
)


def test_a_file_next_to_the_top_hit_in_the_graph_rises_and_missed_neighbours_enter() -> None:
    hybrid = [hit("a.py", 0.0328), hit("d.py", 0.0164), hit("b.py", 0.0100)]
    retriever = GraphExpandedRetriever(first_stage(hybrid), GRAPH, anchors=1)

    results = retriever.search("q", 50)

    vote = 1 / (RRF_K + 1)  # anchor a.py is rank 1
    scores = {r.file_path: r.score for r in results}
    assert scores["a.py"] == pytest.approx(0.0328)  # an anchor with no neighbours voting for it
    assert scores["b.py"] == pytest.approx(0.0100 + vote)  # retrieved AND a neighbour: boosted
    assert scores["c.py"] == pytest.approx(vote)  # missed by retrieval, entered via the graph
    assert scores["d.py"] == pytest.approx(0.0164)  # retrieved, not a neighbour: unchanged
    assert [r.file_path for r in results] == ["a.py", "b.py", "d.py", "c.py"]


def test_expansion_only_files_have_an_empty_representative_chunk() -> None:
    results = GraphExpandedRetriever(first_stage([hit("a.py", 0.03)]), GRAPH, anchors=1).search(
        "q", 50
    )
    entered = next(r for r in results if r.file_path == "c.py")
    assert entered.content == "" and entered.symbol_type == "graph-expansion"
    assert next(r for r in results if r.file_path == "a.py").content == "chunk of a.py"


def test_only_the_top_anchors_vote_and_the_vote_shrinks_with_rank() -> None:
    graph = graph_of(
        {
            "a.py": "import x\n", "b.py": "import y\n", "c.py": "import z\n",
            "x.py": "", "y.py": "", "z.py": "",
        }
    )  # fmt: skip
    hybrid = [hit("a.py", 0.03), hit("b.py", 0.02), hit("c.py", 0.01)]
    results = GraphExpandedRetriever(first_stage(hybrid), graph, anchors=2).search("q", 50)
    scores = {r.file_path: r.score for r in results}
    assert scores["x.py"] == pytest.approx(1 / (RRF_K + 1))
    assert scores["y.py"] == pytest.approx(1 / (RRF_K + 2))
    assert "z.py" not in scores  # c.py is rank 3, outside the two anchors
    assert ANCHORS == 3 and RRF_K == 60


def test_a_neighbour_of_several_anchors_collects_every_vote_once_per_pair() -> None:
    graph = graph_of(
        {
            "a.py": "import shared\n", "b.py": "from shared import f\ndef g():\n    f()\n",
            "shared.py": "def f(): ...\n",
        }
    )  # fmt: skip
    hybrid = [hit("a.py", 0.03), hit("b.py", 0.02)]
    retriever = GraphExpandedRetriever(first_stage(hybrid), graph, anchors=2)
    results = retriever.search("q", 50)
    # b.py imports AND calls into shared.py, but shared.py gets b's vote only once
    assert {r.file_path: r.score for r in results}["shared.py"] == pytest.approx(
        1 / (RRF_K + 1) + 1 / (RRF_K + 2)
    )
    trace = retriever.last_trace
    assert trace is not None
    pair = next(p for p in trace.pairs if p.anchor == "b.py" and p.neighbor == "shared.py")
    assert pair.relations == {"imports": 1, "calls": 1}
    assert {e.relationship for e in pair.edges} == {"imports", "calls"}  # evidence for each
    assert pair.edges[0].evidence.file == "b.py"


def test_ties_are_broken_by_hybrid_rank_then_path() -> None:
    graph = graph_of({"a.py": "", "b.py": "", "c.py": ""})  # no edges
    hybrid = [hit("b.py", 0.01), hit("a.py", 0.01), hit("c.py", 0.01)]
    results = GraphExpandedRetriever(first_stage(hybrid), graph).search("q", 50)
    assert [r.file_path for r in results] == ["b.py", "a.py", "c.py"]  # hybrid order kept


def test_no_neighbours_means_the_hybrid_ranking_is_unchanged_and_depth_is_passed() -> None:
    seen: list[int] = []
    hybrid = [hit("d.py", 0.03), hit("b.py", 0.02)]
    retriever = GraphExpandedRetriever(first_stage(hybrid, seen), GRAPH, anchors=1)
    # d.py has no graph neighbours at all
    results = retriever.search("q", 50)
    assert [r.file_path for r in results] == ["d.py", "b.py"]
    assert seen == [50]
    assert retriever.last_trace is not None and retriever.last_trace.pairs == []


def test_the_trace_records_ranks_before_and_after_and_timings() -> None:
    hybrid = [hit("a.py", 0.03), hit("d.py", 0.02), hit("b.py", 0.01)]
    retriever = GraphExpandedRetriever(first_stage(hybrid), GRAPH, anchors=1)
    retriever.search("q", 50)
    trace = retriever.last_trace
    assert trace is not None
    assert trace.anchors == ["a.py"]
    entries = {e.file_path: e for e in trace.final}
    assert (entries["b.py"].hybrid_rank, entries["b.py"].final_rank) == (3, 2)
    assert entries["c.py"].hybrid_rank is None and entries["c.py"].graph_vote > 0
    assert entries["d.py"].graph_vote == 0
    assert trace.first_stage_ms >= 0 and trace.expand_ms >= 0
    assert {p.neighbor_category for p in trace.pairs} == {"source"}


def test_empty_first_stage() -> None:
    retriever = GraphExpandedRetriever(first_stage([]), GRAPH)
    assert retriever.search("q", 50) == []
