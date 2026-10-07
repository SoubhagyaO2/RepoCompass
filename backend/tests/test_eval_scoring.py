import pytest

from app.evaluation.dataset import EvalDataset, EvalQuestion
from app.evaluation.runner import evaluate
from app.evaluation.scoring import ScopeResult, score_ranking, summarize, unique_files
from app.models.search import SearchHit

APP, CTX, CONFIG, CLI = (f"src/flask/{n}.py" for n in ("app", "ctx", "config", "cli"))


# --- unique_files ---------------------------------------------------------------------


def test_unique_files_keeps_first_occurrence_in_rank_order() -> None:
    assert unique_files([APP, APP, CTX, CONFIG, CTX]) == [APP, CTX, CONFIG]
    assert unique_files([]) == []


# --- score_ranking --------------------------------------------------------------------


def test_multiple_relevant_files_hit_if_any_is_in_top_k() -> None:
    # The worked example: ground truth app.py + ctx.py, retrieval config, ctx, cli.
    result = score_ranking([CONFIG, CTX, CLI], {APP, CTX}, k=5)
    assert result == ScopeResult(
        first_relevant_rank=2, hit_at_1=False, hit_at_k=True, reciprocal_rank=0.5
    )


def test_first_result_relevant() -> None:
    result = score_ranking([APP, CTX], {APP}, k=5)
    assert (result.first_relevant_rank, result.hit_at_1, result.hit_at_k) == (1, True, True)
    assert result.reciprocal_rank == 1.0


def test_relevant_file_beyond_k_is_a_miss_at_k_but_still_has_a_reciprocal_rank() -> None:
    files = [f"src/flask/f{i}.py" for i in range(1, 8)]
    result = score_ranking(files, {files[6]}, k=5)  # rank 7
    assert result.first_relevant_rank == 7
    assert not result.hit_at_1 and not result.hit_at_k
    assert result.reciprocal_rank == pytest.approx(1 / 7)


def test_no_relevant_file_retrieved_scores_zero() -> None:
    result = score_ranking([CONFIG, CLI], {APP}, k=5)
    assert result == ScopeResult(
        first_relevant_rank=None, hit_at_1=False, hit_at_k=False, reciprocal_rank=0.0
    )
    assert score_ranking([], {APP}, k=5).first_relevant_rank is None


def test_primary_and_primary_plus_supporting_can_differ() -> None:
    ranked = [CTX, APP]  # the supporting file is found first, the primary second
    primary_only = score_ranking(ranked, {APP}, k=5)
    with_supporting = score_ranking(ranked, {APP, CTX}, k=5)
    assert primary_only.first_relevant_rank == 2
    assert with_supporting.first_relevant_rank == 1


def test_top_k_counts_unique_files_not_chunks() -> None:
    # Six chunks from one file, then the relevant file: chunk rank 7, but unique-file rank 2.
    chunks = [APP] * 6 + [CTX]
    assert score_ranking(unique_files(chunks), {CTX}, k=5).hit_at_k is True


# --- summarize ------------------------------------------------------------------------


def test_summarize_averages_hits_and_reciprocal_ranks() -> None:
    results = [
        score_ranking([APP], {APP}, k=5),  # rank 1
        score_ranking([CONFIG, APP], {APP}, k=5),  # rank 2
        score_ranking([CONFIG], {APP}, k=5),  # miss
        score_ranking([CLI, CONFIG, CTX, APP, "x", "y"], {APP}, k=5),  # rank 4
    ]
    s = summarize(results)
    assert s.n == 4
    assert s.top1 == 0.25
    assert s.topk == 0.75
    assert s.mrr == pytest.approx((1 + 1 / 2 + 0 + 1 / 4) / 4)


def test_summarize_empty() -> None:
    assert summarize([]).n == 0


# --- runner ---------------------------------------------------------------------------


def make_question(id: str, category: str, difficulty: str, primary: list[str], **kw: object):  # type: ignore[no-untyped-def]
    return EvalQuestion.model_validate(
        {
            "id": id,
            "question": f"question {id}",
            "category": category,
            "difficulty": difficulty,
            "primary": primary,
            "evidence": "e",
            **kw,
        }
    )


def hit(path: str) -> SearchHit:
    return SearchHit(
        file_path=path, start_line=1, end_line=2, score=1.0, content="x", category="source"
    )


def test_evaluate_scores_each_question_and_groups_by_category_and_difficulty() -> None:
    ds = EvalDataset(
        name="t",
        repository="a/b",
        commit="0" * 40,
        description="",
        methodology={},
        questions=[
            make_question("q1", "definition", "easy", [APP]),
            make_question("q2", "concept", "hard", [APP], supporting=[CTX]),
        ],
    )
    calls: list[tuple[str, int]] = []

    def retrieve(query: str, depth: int) -> list[SearchHit]:
        calls.append((query, depth))
        # q1: app.py first. q2: duplicates of config.py, then ctx.py, then app.py.
        if query == "question q1":
            return [hit(APP), hit(CTX)]
        return [hit(CONFIG), hit(CONFIG), hit(CTX), hit(APP)]

    outcome = evaluate(ds, retrieve, depth=50, k=5)

    assert calls == [("question q1", 50), ("question q2", 50)]  # depth is passed through
    q1, q2 = outcome.questions
    assert q1.retrieved_files == [APP, CTX]
    assert q2.retrieved_files == [CONFIG, CTX, APP]  # deduplicated
    assert q2.primary.first_relevant_rank == 3  # app.py
    assert q2.primary_plus_supporting.first_relevant_rank == 2  # ctx.py counts here
    assert q1.latency_ms >= 0

    assert outcome.primary.overall.top1 == 0.5
    assert outcome.primary.overall.mrr == pytest.approx((1 + 1 / 3) / 2)
    assert outcome.primary_plus_supporting.overall.mrr == pytest.approx((1 + 1 / 2) / 2)
    assert outcome.primary.by_category["definition"].top1 == 1.0
    assert outcome.primary.by_category["concept"].n == 1
    assert outcome.primary.by_category["flow"].n == 0  # categories with no questions: n=0
    assert outcome.primary.by_difficulty["hard"].mrr == pytest.approx(1 / 3)
    assert outcome.latency.max_ms >= outcome.latency.median_ms >= 0
