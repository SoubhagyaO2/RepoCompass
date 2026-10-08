"""Milestone 10B: graph-enhanced retrieval vs the hybrid it expands.

    python -m app.evaluation.run_graph [--report-only]

Method, comparisons and statistics are pre-registered in evaluation/plans/m10-code-graph.md
(committed before any graph code existed). Needs the Milestone 8 indexes (run run_hybrid first);
the first stage must reproduce the saved Milestone 8 rankings exactly.
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from app.evaluation.compare import compare_runs
from app.evaluation.compare import format_comparison as format_paired
from app.evaluation.dataset import load_dataset
from app.evaluation.experiment import (
    DEFAULT_DATASET,
    RESULTS_DIR,
    RETRIEVAL_DEPTH,
    TOP_K,
    ExperimentError,
    Prepared,
    build_report,
    format_comparison,
    format_report,
    prepare,
    save_report,
)
from app.evaluation.graph_answers import compare_with_benchmark
from app.evaluation.run_hybrid import _bm25_fields, _embedding_fields, _fusion_fields
from app.evaluation.run_rerank import HYBRID_FILES, _check_first_stage, _first_stages
from app.evaluation.runner import evaluate
from app.evaluation.scoring import ScopeResult, summarize
from app.graph.builder import build_graph
from app.graph.model import CodeGraph
from app.models.search import SearchHit
from app.retrieval.embedder import get_embedder
from app.retrieval.graph_expansion import ANCHORS, ExpansionTrace, GraphExpandedRetriever
from app.retrieval.hybrid import RRF_K, SearchFn
from app.retrieval.vector_store import VectorStore

GRAPH_FILES = {
    "fixed": "flask_fixed_chunk_hybrid_graph.json",
    "ast": "flask_ast_chunk_hybrid_graph.json",
}
GRAPH_VS_BENCHMARK = "graph_vs_benchmark_flask.json"
NAMES = {"fixed": ("H1", "G1"), "ast": ("H2", "G2")}
CATEGORIES = ("definition", "caller", "dependency", "concept", "flow")

LATENCY_NOTE = (
    "per query: hybrid first stage (BM25 + embed + vector search + file-level fusion) plus graph "
    "expansion (neighbour lookup and re-scoring), models and graph already loaded, one query at "
    "a time on CPU, after one untimed warm-up query. The graph build is reported separately."
)


def _run(chunking: str, prepared: Prepared, first_stage: SearchFn, graph: CodeGraph,
         graph_seconds: float, embedder: Any) -> dict[str, Any]:  # fmt: skip
    ds, settings = prepared.dataset, prepared.settings
    retriever = GraphExpandedRetriever(first_stage, graph, ANCHORS, RRF_K)
    traces: dict[str, ExpansionTrace] = {}

    def search(query: str, depth: int) -> list[SearchHit]:
        hits = retriever.search(query, depth)
        assert retriever.last_trace is not None
        traces[query] = retriever.last_trace
        return hits

    search("warm-up query", RETRIEVAL_DEPTH)
    traces.clear()
    outcome = evaluate(ds, search, RETRIEVAL_DEPTH, TOP_K)

    size, overlap = settings.baseline_chunk_size, settings.baseline_chunk_overlap
    s = graph.stats
    method: dict[str, Any] = {
        "retrieval_method": f"{chunking}_chunk_hybrid_graph",
        "method_description": f"{NAMES[chunking][1]}: {chunking} chunks, BM25 + embeddings "
        f"(RRF k={RRF_K}) then graph expansion of the top {ANCHORS} files",
        "chunking": chunking,
        **({"chunk_size": size, "chunk_overlap": overlap} if chunking == "fixed" else {}),
        "first_stage": "hybrid, identical to Milestone 8 (verified question by question)",
        **_embedding_fields(embedder, prepared),
        **_bm25_fields(),
        **_fusion_fields(),
        "graph": {
            "scope": "all Python files in the repository, tests and examples included",
            "files": s.files,
            "nodes_by_type": s.nodes_by_type,
            "edges_by_type": s.edges_by_type,
            "calls_resolved_by_rule": s.calls_resolved_by_rule,
            "build_seconds_including_parsing": graph_seconds,
            "validation": "evaluation/results/graph_validation_flask.json",
        },
        "expansion": {
            "anchors": ANCHORS,
            "neighbor_relations": ["imports", "imported_by", "calls", "called_by"],
            "vote": "1 / (60 + anchor rank), once per (anchor, neighbour) pair",
            "score": "hybrid RRF score (0 if the hybrid did not return the file) + graph votes",
            "tie_break": "hybrid rank, then path",
            "evidence": "full example edges for pairs whose neighbour ends in the final top 10; "
            "relation counts for all pairs",
        },
        "tuned_on_benchmark": False,
        "plan": "evaluation/plans/m10-code-graph.md",
    }
    report = build_report(
        ds,
        outcome,
        method=method,
        indexing=None,
        latency_note=LATENCY_NOTE,
        packages=("sentence-transformers", "torch", "qdrant-client", "networkx"),
        index_reused=True,
    )
    report["expansion_traces"] = [
        {
            "id": q.id,
            **traces[q.question].model_dump(exclude={"first_stage_ms", "expand_ms"}),
            "first_stage_ms": traces[q.question].first_stage_ms,
            "expand_ms": traces[q.question].expand_ms,
        }  # fmt: skip
        for q in ds.questions
    ]
    return report


# --- statistics from the saved files -------------------------------------------------------


def _rank(question: dict[str, Any]) -> float:
    r = question["primary"]["first_relevant_rank"]
    return float(r) if r is not None else float("inf")


def movement(before: dict[str, Any], after: dict[str, Any]) -> dict[str, dict[str, int]]:
    """Pre-registered descriptive statistics on the first primary file's rank, by category."""
    keys = ("n", "up", "down", "same", "brought_in", "pushed_out")
    groups = {c: dict.fromkeys(keys, 0) for c in (*CATEGORIES, "OVERALL")}
    for b, a in zip(before["per_question"], after["per_question"], strict=True):
        rb, ra = _rank(b), _rank(a)
        for g in (groups[b["category"]], groups["OVERALL"]):
            g["n"] += 1
            g["up"] += ra < rb
            g["down"] += ra > rb
            g["same"] += ra == rb
            g["brought_in"] += rb > 20 and ra <= TOP_K
            g["pushed_out"] += rb <= TOP_K and ra > TOP_K
    return groups


def _without(report: dict[str, Any], category: str) -> dict[str, Any]:
    return {
        **report,
        "per_question": [q for q in report["per_question"] if q["category"] != category],
    }


def _overall(report: dict[str, Any], exclude: str | None = None) -> str:
    qs = [q for q in report["per_question"] if q["category"] != exclude]
    m = summarize(ScopeResult.model_validate(q["primary"]) for q in qs)
    return f"{m.top1:.1%} / {m.topk:.1%} / {m.mrr:.3f}"


def _format_movement(title: str, groups: dict[str, dict[str, int]]) -> str:
    lines = [
        title,
        f"  {'':<12}{'n':>4}{'up':>5}{'down':>6}{'same':>6}{'brought in':>12}{'pushed out':>12}",
    ]
    for key in ("OVERALL", *CATEGORIES):
        g = groups[key]
        lines.append(
            f"  {key:<12}{g['n']:>4}{g['up']:>5}{g['down']:>6}{g['same']:>6}"
            f"{g['brought_in']:>12}{g['pushed_out']:>12}"
        )
    return "\n".join(lines)


def summarize_saved(out_dir: Path, dataset_path: Path = DEFAULT_DATASET) -> str:
    """All tables and the pre-registered comparisons, computed from the saved files only."""
    load = lambda name: json.loads((out_dir / name).read_text(encoding="utf-8"))  # noqa: E731
    hybrid = {c: load(HYBRID_FILES[c]) for c in HYBRID_FILES}
    graph = {c: load(GRAPH_FILES[c]) for c in GRAPH_FILES}

    parts = [
        "Overall, primary scope (Top-1 / Top-5 / MRR):\n"
        f"  {'':<8}{'Hybrid':^26}{'Hybrid + graph':^26}"
        + "".join(
            f"\n  {c.upper():<8}{_overall(hybrid[c]):^26}{_overall(graph[c]):^26}"
            for c in ("fixed", "ast")
        ),
        "Excluding the dependency category (61 questions), primary scope:\n"
        f"  {'':<8}{'Hybrid':^26}{'Hybrid + graph':^26}"
        + "".join(
            f"\n  {c.upper():<8}{_overall(hybrid[c], 'dependency'):^26}"
            f"{_overall(graph[c], 'dependency'):^26}"
            for c in ("fixed", "ast")
        ),
    ]
    for c in ("fixed", "ast"):
        group = [("HYBRID", hybrid[c]), ("+GRAPH", graph[c])]
        parts.append(f"{c.upper()} chunks, by category and difficulty\n" + format_comparison(group))

    comparisons = [
        ("1. G1 vs H1", hybrid["fixed"], graph["fixed"], "H1", "G1"),
        ("2. G2 vs H2", hybrid["ast"], graph["ast"], "H2", "G2"),
        ("3. G2 vs G1", graph["fixed"], graph["ast"], "G1", "G2"),
    ]
    lines = ["Pre-registered paired comparisons (B vs A; Bonferroni sign-test threshold 0.0167):"]
    for label, a, b, name_a, name_b in comparisons:
        lines.append(
            f"\n{label}, all 73 questions\n"
            + format_paired(name_a, name_b, compare_runs(a, b), TOP_K)
        )
        excl = compare_runs(_without(a, "dependency"), _without(b, "dependency"))
        lines.append(
            f"{label}, excluding dependency (61)\n" + format_paired(name_a, name_b, excl, TOP_K)
        )
    parts.append("\n".join(lines))

    for c in ("fixed", "ast"):
        parts.append(
            _format_movement(
                f"{NAMES[c][1]} vs {NAMES[c][0]}: first primary file, rank before vs after",
                movement(hybrid[c], graph[c]),
            )
        )

    notes = []
    for c in ("fixed", "ast"):
        traces = graph[c]["expansion_traces"]
        pairs = [p for t in traces for p in t["pairs"]]
        non_source = sum(p["neighbor_category"] != "source" for p in pairs)
        top5_new = sum(
            1
            for t in traces
            for e in t["final"]
            if e["final_rank"] <= TOP_K and e["hybrid_rank"] is None
        )
        notes.append(
            f"{NAMES[c][1]}: {len(pairs):,} (anchor, neighbour) pairs over 73 questions "
            f"(mean {len(pairs) / len(traces):.0f} per question); {non_source / len(pairs):.0%} of "
            f"neighbours are test/docs files; {top5_new} of {73 * TOP_K} top-5 slots hold a file "
            f"retrieval had not returned"
        )
        fs = sorted(t["first_stage_ms"] for t in traces)[len(traces) // 2]
        ex = sorted(t["expand_ms"] for t in traces)[len(traces) // 2]
        notes.append(
            f"      latency median: first stage {fs:.1f} ms, expansion {ex:.2f} ms, end-to-end "
            f"{graph[c]['results']['latency_ms']['median_ms']:.1f} ms; graph build "
            f"{graph[c]['graph']['build_seconds_including_parsing']:.2f} s"
        )
    parts.append("Expansion\n  " + "\n  ".join(notes))

    info_path = out_dir / GRAPH_VS_BENCHMARK
    if info_path.exists():
        summary = json.loads(info_path.read_text(encoding="utf-8"))["summary"]
        rows = [
            f"  {cat:<11} n={s['n']}  exact match {s['exact_match']}/{s['n']}  "
            f"mean precision {s['mean_precision']:.2f}  mean recall {s['mean_recall']:.2f}"
            for cat, s in summary.items()
        ]
        parts.append(
            "The graph's own answers vs the benchmark's primary files (descriptive; dependency is "
            "circular):\n" + "\n".join(rows)
        )
    return "\n\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.evaluation.run_graph", description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--out-dir", type=Path, default=RESULTS_DIR)
    parser.add_argument("--report-only", action="store_true", help="print from saved results only")
    args = parser.parse_args(argv)
    if args.report_only:
        print(summarize_saved(args.out_dir, args.dataset))
        return 0

    try:
        prepared = prepare(args.dataset)
    except ExperimentError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    embedder = get_embedder(prepared.settings)

    t0 = time.perf_counter()
    graph = build_graph(prepared.ingested)
    graph_seconds = time.perf_counter() - t0
    print(f"graph built in {graph_seconds:.2f}s: {graph.stats.edges_by_type}")

    with VectorStore(prepared.settings.qdrant_path) as store:
        try:
            stages = _first_stages(prepared, embedder, store)
            for chunking, first_stage in stages.items():
                _check_first_stage(prepared, chunking, first_stage)
        except ExperimentError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        print("first stage reproduces the saved Milestone 8 hybrid rankings for all questions")

        for chunking, first_stage in stages.items():
            report = _run(chunking, prepared, first_stage, graph, graph_seconds, embedder)
            out = args.out_dir / GRAPH_FILES[chunking]
            save_report(report, out)
            print("\n" + format_report(report))
            print(f"Saved to {out}")

    ds = load_dataset(args.dataset)
    info = compare_with_benchmark(graph, ds.questions)
    (args.out_dir / GRAPH_VS_BENCHMARK).write_text(
        json.dumps(info, indent=2) + "\n", encoding="utf-8"
    )
    print("\n" + summarize_saved(args.out_dir, args.dataset))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
