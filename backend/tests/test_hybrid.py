import pytest

from app.models.search import SearchHit
from app.retrieval.hybrid import (
    RRF_K,
    HybridRetriever,
    reciprocal_rank_fusion,
    unique_ranked_files,
)


def hit(path: str, line: int = 1, score: float = 1.0) -> SearchHit:
    return SearchHit(
        file_path=path, start_line=line, end_line=line + 1, score=score, content=path,
        category="source",
    )  # fmt: skip


# --- reciprocal_rank_fusion -----------------------------------------------------------


def test_rrf_is_the_sum_of_one_over_k_plus_rank() -> None:
    bm25 = ["a.py", "b.py", "c.py"]
    emb = ["b.py", "d.py", "a.py"]
    scores = dict(reciprocal_rank_fusion([bm25, emb], k=60))

    assert scores["a.py"] == pytest.approx(1 / 61 + 1 / 63)  # rank 1 and rank 3
    assert scores["b.py"] == pytest.approx(1 / 62 + 1 / 61)  # rank 2 and rank 1
    assert scores["c.py"] == pytest.approx(1 / 63)  # only in one list: one vote
    assert scores["d.py"] == pytest.approx(1 / 62)


def test_a_file_both_retrievers_like_beats_a_file_only_one_ranks_first() -> None:
    fused = [path for path, _ in reciprocal_rank_fusion([["x.py", "a.py"], ["y.py", "a.py"]])]
    assert fused[0] == "a.py"  # rank 2 in both beats rank 1 in just one
    assert set(fused) == {"x.py", "y.py", "a.py"}


def test_rrf_ties_break_by_best_rank_then_path() -> None:
    # x.py is rank 1 in list A only; y.py is rank 1 in list B only: equal scores, equal ranks.
    fused = [p for p, _ in reciprocal_rank_fusion([["x.py"], ["y.py"]])]
    assert fused == ["x.py", "y.py"]  # path order
    # same score, but z has the better single rank
    fused = [p for p, _ in reciprocal_rank_fusion([["p.py", "z.py"], ["q.py", "w.py"], ["z.py"]])]
    assert fused[0] == "z.py"


def test_rrf_edge_cases() -> None:
    assert reciprocal_rank_fusion([]) == []
    assert reciprocal_rank_fusion([[], []]) == []
    assert reciprocal_rank_fusion([["only.py"]]) == [("only.py", pytest.approx(1 / 61))]
    assert RRF_K == 60


# --- file-level fusion ----------------------------------------------------------------


def test_unique_ranked_files_keeps_each_files_best_position() -> None:
    hits = [hit("a.py", 1), hit("a.py", 9), hit("b.py"), hit("a.py", 20), hit("c.py")]
    assert unique_ranked_files(hits) == ["a.py", "b.py", "c.py"]


def test_many_chunks_from_one_file_do_not_get_extra_votes() -> None:
    # bm25 returns 15 chunks of big.py, then small.py; the embedder returns small.py first.
    bm25 = [hit("big.py", i) for i in range(15)] + [hit("small.py")]
    emb = [hit("small.py")]
    results = HybridRetriever([lambda q, d: bm25, lambda q, d: emb]).search("q", 50)

    assert [h.file_path for h in results] == ["small.py", "big.py"]
    scores = {h.file_path: h.score for h in results}
    assert scores["big.py"] == pytest.approx(1 / 61)  # one vote, not fifteen
    assert scores["small.py"] == pytest.approx(1 / 62 + 1 / 61)


# --- HybridRetriever ------------------------------------------------------------------


def test_hybrid_passes_depth_to_every_component_and_returns_unique_files() -> None:
    seen: list[tuple[str, int]] = []

    def component(name: str, files: list[str]):  # type: ignore[no-untyped-def]
        def search(query: str, depth: int) -> list[SearchHit]:
            seen.append((name, depth))
            return [hit(f, i) for i, f in enumerate(files)]

        return search

    results = HybridRetriever(
        [component("bm25", ["a.py", "b.py"]), component("emb", ["b.py", "c.py"])]
    ).search("question", 50)

    assert seen == [("bm25", 50), ("emb", 50)]
    assert [h.file_path for h in results] == ["b.py", "a.py", "c.py"]
    assert len({h.file_path for h in results}) == 3
    assert results[0].score > results[1].score >= results[2].score


def test_each_file_is_represented_by_its_best_ranked_chunk() -> None:
    bm25 = [hit("a.py", line=10), hit("b.py", line=1)]
    emb = [hit("b.py", line=99), hit("a.py", line=50)]
    results = {
        h.file_path: h
        for h in HybridRetriever([lambda q, d: bm25, lambda q, d: emb]).search("q", 5)
    }
    # a.py: rank 1 in bm25 (line 10) beats rank 2 in emb; b.py: rank 1 in emb (line 99) beats rank 2
    assert results["a.py"].start_line == 10
    assert results["b.py"].start_line == 99


def test_hybrid_with_an_empty_component_falls_back_to_the_other() -> None:
    results = HybridRetriever([lambda q, d: [], lambda q, d: [hit("only.py")]]).search("q", 5)
    assert [h.file_path for h in results] == ["only.py"]
    assert HybridRetriever([lambda q, d: []]).search("q", 5) == []
