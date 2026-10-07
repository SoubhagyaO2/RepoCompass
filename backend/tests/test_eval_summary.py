from pathlib import Path

import pytest

from app.evaluation.experiment import RESULTS_DIR
from app.evaluation.run_hybrid import FILES, PREREGISTERED_COMPARISONS, summarize_saved
from app.evaluation.run_rerank import RERANK_FILES
from app.evaluation.run_rerank import summarize_saved as summarize_rerank

pytestmark = pytest.mark.skipif(
    not all((RESULTS_DIR / name).exists() for name in FILES.values()),
    reason="the Milestone 8 results are not saved yet",
)


def test_preregistered_comparisons_are_the_six_agreed_ones() -> None:
    assert [label.split(".")[0] for label, _, _ in PREREGISTERED_COMPARISONS] == list("123456")
    assert all(a in FILES and b in FILES for _, a, b in PREREGISTERED_COMPARISONS)


def test_summary_is_computed_from_the_saved_files_and_labels_every_comparison() -> None:
    text = summarize_saved(Path(RESULTS_DIR))
    assert "Embeddings" in text and "Hybrid (RRF)" in text
    assert "fixed+hybrid vs fixed+embeddings" in text  # comparison 1, named by chunking + method
    assert "ast+hybrid vs fixed+hybrid" in text  # comparison 6
    assert text.count("sign test p =") == 6


@pytest.mark.skipif(
    not all((RESULTS_DIR / name).exists() for name in RERANK_FILES.values()),
    reason="the Milestone 9 results are not saved yet",
)
def test_rerank_summary_has_the_three_comparisons_and_the_preserved_candidate_pools() -> None:
    import json

    text = summarize_rerank(Path(RESULTS_DIR))
    assert text.count("sign test p =") == 3
    assert "rank before vs after" in text and "rescued" in text and "pushed out" in text

    for name in RERANK_FILES.values():
        report = json.loads((RESULTS_DIR / name).read_text(encoding="utf-8"))
        assert len(report["candidate_pools"]) == report["n_questions"]
        for pool in report["candidate_pools"]:
            ranks = sorted(c["reranker_rank"] for c in pool["candidates"])
            assert ranks == list(range(1, len(ranks) + 1))  # a proper ranking of the pool
            assert {"retrieval_rank", "retrieval_score", "reranker_score"} <= set(
                pool["candidates"][0]
            )
