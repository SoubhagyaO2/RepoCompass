import json
from pathlib import Path

import pytest

from app.evaluation.experiment import RESULTS_DIR
from app.evaluation.run_graph import GRAPH_FILES, summarize_saved

pytestmark = pytest.mark.skipif(
    not all((RESULTS_DIR / name).exists() for name in GRAPH_FILES.values()),
    reason="the Milestone 10B results are not saved yet",
)


def test_summary_has_the_three_comparisons_with_and_without_the_dependency_category() -> None:
    text = summarize_saved(Path(RESULTS_DIR))
    assert text.count("sign test p =") == 6
    assert text.count("excluding dependency (61)") == 3  # one per comparison
    assert "Excluding the dependency category (61 questions)" in text  # the overall table
    assert "brought in" in text and "pushed out" in text


def test_every_question_keeps_its_expansion_evidence_and_a_consistent_final_ranking() -> None:
    for name in GRAPH_FILES.values():
        report = json.loads((RESULTS_DIR / name).read_text(encoding="utf-8"))
        assert report["tuned_on_benchmark"] is False
        assert len(report["expansion_traces"]) == report["n_questions"]
        for trace in report["expansion_traces"]:
            ranks = [e["final_rank"] for e in trace["final"]]
            assert ranks == sorted(ranks) and ranks[0] == 1
            scores = [e["fused_score"] for e in trace["final"]]
            assert scores == sorted(scores, reverse=True)
            for entry in trace["final"]:
                assert entry["fused_score"] == pytest.approx(
                    entry["hybrid_score"] + entry["graph_vote"]
                )
            final_rank = {e["file_path"]: e["final_rank"] for e in trace["final"]}
            for pair in trace["pairs"]:
                assert pair["anchor"] in trace["anchors"]
                rank = final_rank.get(pair["neighbor"])
                if rank is not None and rank <= 10:  # evidence is attached where it matters
                    assert pair["edges"], (trace["id"], pair["neighbor"])
                    edge = pair["edges"][0]
                    assert {"source", "relationship", "target", "evidence"} <= set(edge)
