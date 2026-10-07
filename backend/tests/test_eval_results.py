"""Guards against stale or inconsistent saved benchmark results."""

import json
from pathlib import Path

import pytest

from app.evaluation.dataset import load_dataset
from app.evaluation.run_baseline import RETRIEVAL_DEPTH, TOP_K

EVAL_DIR = Path(__file__).resolve().parents[2] / "evaluation"
RESULTS = sorted((EVAL_DIR / "results").glob("*.json"))


def test_scoring_parameters_are_locked() -> None:
    assert (TOP_K, RETRIEVAL_DEPTH) == (5, 50)


@pytest.mark.skipif(not RESULTS, reason="no saved results yet")
@pytest.mark.parametrize("path", RESULTS, ids=lambda p: p.name)
def test_saved_results_match_the_frozen_dataset_and_locked_scoring(path: Path) -> None:
    result = json.loads(path.read_text(encoding="utf-8"))
    repo_name = result["dataset_repository"].split("/")[1]  # "pallets/flask" -> flask.json
    ds = load_dataset(EVAL_DIR / "datasets" / f"{repo_name}.json")
    assert result["dataset"] == ds.name

    assert result["dataset_fingerprint"] == ds.questions_sha256  # not run on an edited dataset
    assert result["dataset_commit"] == ds.commit
    assert result["n_questions"] == len(ds.questions)
    assert [q["id"] for q in result["per_question"]] == [q.id for q in ds.questions]
    assert result["top_k"] == TOP_K
    assert result["retrieval_depth_chunks"] == RETRIEVAL_DEPTH
    assert result["deduplication"] == "first_file_occurrence"
    assert result["scoring"] == "file_level"
    for scope in ("primary", "primary_plus_supporting"):
        assert result["results"][scope]["overall"]["n"] == len(ds.questions)
