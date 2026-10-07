import pytest

from app.evaluation.compare import compare_runs, sign_test_p


def scope(rank: int | None, k: int = 5) -> dict[str, object]:
    return {
        "first_relevant_rank": rank,
        "hit_at_1": rank == 1,
        "hit_at_k": rank is not None and rank <= k,
        "reciprocal_rank": 1 / rank if rank else 0.0,
    }


def run(ranks: list[int | None]) -> dict[str, object]:
    return {
        "top_k": 5,
        "per_question": [
            {"id": f"q{i}", "primary": scope(r), "primary_plus_supporting": scope(r)}
            for i, r in enumerate(ranks)
        ],
    }


def test_sign_test_matches_the_exact_binomial() -> None:
    assert sign_test_p(0, 0) == 1.0
    assert sign_test_p(5, 5) == 1.0
    # 8 vs 2 of 10: P(X<=2) = (1 + 10 + 45) / 1024, doubled
    assert sign_test_p(8, 2) == pytest.approx(2 * 56 / 1024)
    assert sign_test_p(2, 8) == sign_test_p(8, 2)
    assert sign_test_p(10, 0) == pytest.approx(2 / 1024)


def test_wins_losses_ties_and_mean_differences() -> None:
    a = run([1, 3, None, 2, 5])
    b = run([1, 1, 4, 6, None])
    c = compare_runs(a, b)

    # q0 tie; q1 3->1 win; q2 None->4 win; q3 2->6 loss; q4 5->None loss
    assert (c.wins, c.losses, c.ties) == (2, 2, 1)
    assert c.sign_test_p == 1.0
    assert c.top1_diff == pytest.approx(1 / 5)  # b gains q1
    # top-5 hits: a has q0,q1,q3,q4 (4); b has q0,q1,q2 (3). b gains q2, loses q3 (rank 6), q4.
    assert c.topk_diff == pytest.approx(-1 / 5)
    assert c.mrr_diff == pytest.approx(
        ((1 - 1 / 3) + (1 / 4 - 0) + (1 / 6 - 1 / 2) + (0 - 1 / 5)) / 5
    )


def test_identical_runs_have_zero_difference_and_a_degenerate_interval() -> None:
    a = run([1, 2, None])
    c = compare_runs(a, a)
    assert (c.wins, c.losses, c.ties) == (0, 0, 3)
    assert c.mrr_diff == 0 and c.mrr_diff_ci95 == (0.0, 0.0)
    assert c.sign_test_p == 1.0


def test_bootstrap_is_deterministic_and_brackets_the_mean() -> None:
    a = run([5, 4, 3, 2, 6, 7, 3, 4])
    b = run([1, 1, 2, 1, 2, 1, 1, 2])
    c1, c2 = compare_runs(a, b), compare_runs(a, b)
    assert c1 == c2  # fixed seed
    lo, hi = c1.mrr_diff_ci95
    assert lo <= c1.mrr_diff <= hi
    assert lo > 0  # b is better on every question


def test_runs_must_cover_the_same_questions() -> None:
    a, b = run([1, 2]), run([1, 2])
    b["per_question"][1]["id"] = "other"  # type: ignore[index]
    with pytest.raises(ValueError, match="same questions"):
        compare_runs(a, b)
