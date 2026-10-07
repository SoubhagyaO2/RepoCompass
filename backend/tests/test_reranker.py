import pytest

from app.models.search import SearchHit
from app.retrieval.reranker import DEFAULT_CANDIDATES, RerankingRetriever


def hit(path: str, content: str = "", rrf: float = 0.5, line: int = 1) -> SearchHit:
    return SearchHit(
        file_path=path, start_line=line, end_line=line + 2, score=rrf, content=content,
        category="source",
    )  # fmt: skip


class OverlapReranker:
    """Scores a passage by how many query words it contains."""

    model_name = "fake-overlap"
    max_length = 512

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []

    def score(self, query: str, passages: list[str]) -> list[float]:
        self.calls.append((query, passages))
        words = set(query.lower().split())
        return [float(len(words & set(p.lower().split()))) for p in passages]

    def count_pair_tokens(self, query: str, passages: list[str]) -> list[int]:
        return [len(p.split()) for p in passages]


def first_stage(hits: list[SearchHit], seen: list[tuple[str, int]] | None = None):  # type: ignore[no-untyped-def]
    def search(query: str, depth: int) -> list[SearchHit]:
        if seen is not None:
            seen.append((query, depth))
        return hits

    return search


def test_reranker_reorders_candidates_and_preserves_both_ranks() -> None:
    hits = [
        hit("a.py", "unrelated words", rrf=0.9),
        hit("b.py", "session cookie saved here", rrf=0.8),
        hit("c.py", "session", rrf=0.7),
    ]
    reranker = OverlapReranker()
    retriever = RerankingRetriever(first_stage(hits), reranker, candidates=3)

    results = retriever.search("session cookie saved", 50)

    assert [h.file_path for h in results] == ["b.py", "c.py", "a.py"]
    assert [h.score for h in results] == [3.0, 1.0, 0.0]  # reranker scores replace RRF scores
    trace = retriever.last_trace
    assert trace is not None
    by_file = {c.file_path: c for c in trace.candidates}
    assert (by_file["a.py"].retrieval_rank, by_file["a.py"].reranker_rank) == (1, 3)
    assert (by_file["b.py"].retrieval_rank, by_file["b.py"].reranker_rank) == (2, 1)
    assert (by_file["c.py"].retrieval_rank, by_file["c.py"].reranker_rank) == (3, 2)
    assert by_file["b.py"].retrieval_score == 0.8  # the first-stage evidence is kept
    assert by_file["b.py"].reranker_score == 3.0
    assert trace.first_stage_ms >= 0 and trace.rerank_ms >= 0


def test_files_beyond_the_candidate_depth_keep_first_stage_order_after_the_pool() -> None:
    hits = [hit(f"f{i}.py", "x") for i in range(1, 6)]
    hits[0] = hit("f1.py", "nothing")
    hits[1] = hit("f2.py", "match match")  # best in the pool
    reranker = OverlapReranker()
    retriever = RerankingRetriever(first_stage(hits), reranker, candidates=2)

    results = retriever.search("match", 50)

    assert [h.file_path for h in results] == ["f2.py", "f1.py", "f3.py", "f4.py", "f5.py"]
    assert len(reranker.calls[0][1]) == 2  # only the pool is scored
    assert retriever.last_trace is not None and len(retriever.last_trace.candidates) == 2


def test_ties_keep_first_stage_order() -> None:
    hits = [hit("a.py", "none"), hit("b.py", "none"), hit("c.py", "none")]
    results = RerankingRetriever(first_stage(hits), OverlapReranker(), 3).search("zzz", 50)
    assert [h.file_path for h in results] == ["a.py", "b.py", "c.py"]


def test_candidates_are_unique_files_even_if_the_first_stage_repeats_a_file() -> None:
    hits = [hit("a.py", "one", line=1), hit("a.py", "two", line=9), hit("b.py", "three")]
    reranker = OverlapReranker()
    results = RerankingRetriever(first_stage(hits), reranker, 20).search("q", 50)
    assert [h.file_path for h in results] == ["a.py", "b.py"]
    assert reranker.calls[0][1] == ["one", "three"]  # a.py is represented once, by its first chunk


def test_depth_is_passed_to_the_first_stage_and_empty_results_are_handled() -> None:
    seen: list[tuple[str, int]] = []
    retriever = RerankingRetriever(first_stage([], seen), OverlapReranker())
    assert retriever.search("question", 50) == []
    assert seen == [("question", 50)]
    assert retriever.last_trace is not None and retriever.last_trace.candidates == []
    assert DEFAULT_CANDIDATES == 20


def test_count_truncated_uses_the_reranker_limit() -> None:
    long_text = " ".join(["w"] * 600)  # 600 "tokens" for the fake counter
    hits = [hit("a.py", long_text), hit("b.py", "short")]
    retriever = RerankingRetriever(first_stage(hits), OverlapReranker())
    assert retriever.count_truncated("q", 50) == 1


@pytest.mark.parametrize("candidates", [1, 2])
def test_pool_size_is_respected(candidates: int) -> None:
    hits = [hit(f"f{i}.py", "x") for i in range(5)]
    retriever = RerankingRetriever(first_stage(hits), OverlapReranker(), candidates)
    retriever.search("q", 50)
    assert retriever.last_trace is not None
    assert len(retriever.last_trace.candidates) == candidates
