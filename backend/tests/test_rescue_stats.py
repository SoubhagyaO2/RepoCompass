from app.evaluation.run_rerank import rescue_statistics


def cand(path: str, retrieval_rank: int, reranker_rank: int) -> dict[str, object]:
    return {"file_path": path, "retrieval_rank": retrieval_rank, "reranker_rank": reranker_rank}


def test_rescue_statistics_classify_each_question_by_the_first_primary_file() -> None:
    report = {
        "per_question": [
            {"id": "q1", "category": "concept"},  # rescued: rank 9 -> 2
            {"id": "q2", "category": "concept"},  # pushed out: rank 2 -> 7
            {"id": "q3", "category": "flow"},  # same: rank 1 -> 1
            {"id": "q4", "category": "flow"},  # not in the candidate pool at all
            {"id": "q5", "category": "flow"},  # improved but not rescued: 4 -> 3 (already top-5)
            {"id": "q6", "category": "flow"},  # first of two primary files is what counts
        ],
        "candidate_pools": [
            {"id": "q1", "candidates": [cand("a.py", 9, 2), cand("x.py", 1, 5)]},
            {"id": "q2", "candidates": [cand("a.py", 2, 7), cand("x.py", 1, 1)]},
            {"id": "q3", "candidates": [cand("a.py", 1, 1)]},
            {"id": "q4", "candidates": [cand("x.py", 1, 1), cand("y.py", 2, 2)]},
            {"id": "q5", "candidates": [cand("a.py", 4, 3)]},
            {"id": "q6", "candidates": [cand("a.py", 8, 6), cand("b.py", 12, 1)]},
        ],
    }
    primary = {f"q{i}": {"a.py"} for i in range(1, 6)} | {"q6": {"a.py", "b.py"}}

    stats = rescue_statistics(report, primary)

    assert stats["OVERALL"] == {
        "n": 6, "in_pool": 5, "improved": 3, "worsened": 1, "same": 1,
        "rescued": 2, "pushed_out": 1,
    }  # fmt: skip
    # q6: before = min(8, 12) = 8, after = min(6, 1) = 1 -> improved and rescued
    assert stats["concept"]["rescued"] == 1 and stats["concept"]["pushed_out"] == 1
    assert stats["flow"] == {
        "n": 4, "in_pool": 3, "improved": 2, "worsened": 0, "same": 1,
        "rescued": 1, "pushed_out": 0,
    }  # fmt: skip
