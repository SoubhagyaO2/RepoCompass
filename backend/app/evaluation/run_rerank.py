"""Milestone 9: cross-encoder reranking of the hybrid retriever's candidates.

    python -m app.evaluation.run_rerank [--report-only]

Method, comparisons and statistics are pre-registered in evaluation/plans/m09-reranking.md
(committed before this was run). Needs the Milestone 8 indexes (run run_hybrid first); the first
stage must reproduce the saved Milestone 8 rankings exactly before reranking is applied.
"""

import argparse
import json
import sys
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
from app.evaluation.run_hybrid import _bm25_fields, _embedding_fields, _fusion_fields
from app.evaluation.runner import evaluate
from app.models.search import SearchHit
from app.retrieval.baseline import FixedChunkBaseline
from app.retrieval.chunk_bm25 import build_ast_bm25, build_fixed_bm25
from app.retrieval.chunk_embeddings import ChunkEmbeddingRetriever, ast_collection_name
from app.retrieval.embedder import Embedder, get_embedder
from app.retrieval.hybrid import RRF_K, HybridRetriever, SearchFn, unique_ranked_files
from app.retrieval.reranker import (
    DEFAULT_CANDIDATES,
    CrossEncoderReranker,
    RerankingRetriever,
    RerankTrace,
)
from app.retrieval.vector_store import VectorStore

RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
RERANKER_MAX_TOKENS = 512
RERANKER_BATCH_SIZE = 16

HYBRID_FILES = {"fixed": "flask_fixed_chunk_hybrid.json", "ast": "flask_ast_chunk_hybrid.json"}
RERANK_FILES = {
    "fixed": "flask_fixed_chunk_hybrid_rerank.json",
    "ast": "flask_ast_chunk_hybrid_rerank.json",
}
NAMES = {
    "fixed": ("H1 fixed hybrid", "R1 fixed hybrid+rerank"),
    "ast": ("H2 AST hybrid", "R2 AST hybrid+rerank"),
}

LATENCY_NOTE = (
    "per query: hybrid first stage (BM25 + embed + vector search + file-level fusion) plus "
    "cross-encoder reranking of the top candidate files, models already loaded, one query at a "
    "time on CPU, after one untimed warm-up query through the full pipeline"
)


def _latency_stats(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    return {
        "mean_ms": sum(ordered) / len(ordered),
        "median_ms": ordered[len(ordered) // 2],
        "p95_ms": ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))],
        "max_ms": ordered[-1],
    }


# --- first stages and the integrity check ---------------------------------------------


def _first_stages(
    prepared: Prepared, embedder: Embedder, store: VectorStore
) -> dict[str, SearchFn]:
    settings, repo = prepared.settings, prepared.dataset.repository
    size, overlap = settings.baseline_chunk_size, settings.baseline_chunk_overlap

    fixed_emb = FixedChunkBaseline(embedder, store, size, overlap)
    ast_collection = ast_collection_name(repo)
    if not fixed_emb.is_indexed(repo) or not store.collection_exists(ast_collection):
        raise ExperimentError("the embedding indexes are missing; run run_hybrid first")

    fixed_bm25, _ = build_fixed_bm25(prepared.ingested, size, overlap)
    ast_bm25, _ = build_ast_bm25(prepared.ingested)
    ast_emb = ChunkEmbeddingRetriever(embedder, store, ast_collection)

    def fixed_emb_search(query: str, depth: int) -> list[SearchHit]:
        return fixed_emb.search(repo, query, depth)

    return {
        "fixed": HybridRetriever([fixed_bm25.search, fixed_emb_search]).search,
        "ast": HybridRetriever([ast_bm25.search, ast_emb.search]).search,
    }


def _check_first_stage(prepared: Prepared, chunking: str, first_stage: SearchFn) -> None:
    """The first stage must reproduce the saved Milestone 8 ranking for every question."""
    saved = json.loads((RESULTS_DIR / HYBRID_FILES[chunking]).read_text(encoding="utf-8"))
    saved_files = {q["id"]: q["retrieved_files"] for q in saved["per_question"]}
    mismatches = [
        q.id
        for q in prepared.dataset.questions
        if unique_ranked_files(first_stage(q.question, RETRIEVAL_DEPTH))[:10] != saved_files[q.id]
    ]
    if mismatches:
        raise ExperimentError(
            f"the {chunking} first stage no longer reproduces the saved hybrid results for "
            f"{len(mismatches)} questions (e.g. {mismatches[:3]}); refusing to rerank"
        )


# --- the experiment ----------------------------------------------------------------------


def _run(chunking: str, prepared: Prepared, first_stage: SearchFn, embedder: Embedder,
         reranker: CrossEncoderReranker) -> dict[str, Any]:  # fmt: skip
    ds, settings = prepared.dataset, prepared.settings
    retriever = RerankingRetriever(first_stage, reranker, DEFAULT_CANDIDATES)
    traces: dict[str, RerankTrace] = {}

    def search(query: str, depth: int) -> list[SearchHit]:
        hits = retriever.search(query, depth)
        assert retriever.last_trace is not None
        traces[query] = retriever.last_trace
        return hits

    search("warm-up query", RETRIEVAL_DEPTH)  # untimed, through the whole pipeline
    traces.clear()
    outcome = evaluate(ds, search, RETRIEVAL_DEPTH, TOP_K)

    over = {q.id: retriever.count_truncated(q.question, RETRIEVAL_DEPTH) for q in ds.questions}
    pools = [
        {
            "id": q.id,
            "candidates": [c.model_dump() for c in traces[q.question].candidates],
            "first_stage_ms": traces[q.question].first_stage_ms,
            "rerank_ms": traces[q.question].rerank_ms,
            "candidate_chunks_over_reranker_limit": over[q.id],
        }
        for q in ds.questions
    ]
    size, overlap = settings.baseline_chunk_size, settings.baseline_chunk_overlap
    method: dict[str, Any] = {
        "retrieval_method": f"{chunking}_chunk_hybrid_rerank",
        "method_description": f"{NAMES[chunking][1]}: {chunking} chunks, BM25 + embeddings "
        f"(RRF k={RRF_K}) then {RERANKER_MODEL} on the top {DEFAULT_CANDIDATES} files",
        "chunking": chunking,
        **({"chunk_size": size, "chunk_overlap": overlap} if chunking == "fixed" else {}),
        "first_stage": "hybrid, identical to Milestone 8 (verified question by question)",
        **_embedding_fields(embedder, prepared),
        **_bm25_fields(),
        **_fusion_fields(),
        "reranker_model": RERANKER_MODEL,
        "reranker_max_tokens": RERANKER_MAX_TOKENS,
        "reranker_batch_size": RERANKER_BATCH_SIZE,
        "reranker_device": reranker.device,
        "reranker_input": "(question, raw chunk content); no file path or symbol name",
        "reranker_activation": "identity (raw logit)",
        "candidate_files": DEFAULT_CANDIDATES,
        "candidate_unit": "unique file, represented by its best-ranked retrieved chunk",
        "final_ranking": "candidates by reranker score (ties: first-stage order), then the "
        "remaining files in first-stage order",
        "candidate_chunks_over_reranker_limit": sum(over.values()),
        "plan": "evaluation/plans/m09-reranking.md",
    }
    report = build_report(
        ds,
        outcome,
        method=method,
        indexing=None,  # no new index: the Milestone 8 indexes are reused
        latency_note=LATENCY_NOTE,
        packages=("sentence-transformers", "torch", "qdrant-client"),
        index_reused=True,
    )
    report["candidate_pools"] = pools
    report["latency_breakdown_ms"] = {
        "first_stage": _latency_stats([p["first_stage_ms"] for p in pools]),
        "reranker": _latency_stats([p["rerank_ms"] for p in pools]),
    }
    return report


# --- descriptive statistics ---------------------------------------------------------------


def rescue_statistics(report: dict[str, Any], primary: dict[str, set[str]]) -> dict[str, Any]:
    """Pre-registered descriptive statistics from the preserved candidate pools (primary scope).

    For each question the first primary file among the candidates gives its rank before
    (retrieval_rank) and after (reranker_rank) reranking. Questions with no primary file among
    the candidates are out of the reranker's reach.
    """
    category = {q["id"]: q["category"] for q in report["per_question"]}
    keys = ("n", "in_pool", "improved", "worsened", "same", "rescued", "pushed_out")
    blank = dict.fromkeys(keys, 0)
    groups: dict[str, dict[str, int]] = {"OVERALL": dict(blank)}
    for pool in report["candidate_pools"]:
        found = [c for c in pool["candidates"] if c["file_path"] in primary[pool["id"]]]
        for key in ("OVERALL", category[pool["id"]]):
            g = groups.setdefault(key, dict(blank))
            g["n"] += 1
            if not found:
                continue
            before = min(c["retrieval_rank"] for c in found)
            after = min(c["reranker_rank"] for c in found)
            g["in_pool"] += 1
            g["improved"] += after < before
            g["worsened"] += after > before
            g["same"] += after == before
            g["rescued"] += before > TOP_K and after <= TOP_K
            g["pushed_out"] += before <= TOP_K and after > TOP_K
    return groups


def _format_rescue(title: str, stats: dict[str, dict[str, int]]) -> str:
    lines = [
        title,
        f"  {'':<12}{'n':>4}{'in pool':>9}{'improved':>10}{'worsened':>10}{'same':>6}"
        f"{'rescued':>9}{'pushed out':>12}",
    ]
    for key in ("OVERALL", "definition", "caller", "dependency", "concept", "flow"):
        g = stats[key]
        lines.append(
            f"  {key:<12}{g['n']:>4}{g['in_pool']:>9}{g['improved']:>10}{g['worsened']:>10}"
            f"{g['same']:>6}{g['rescued']:>9}{g['pushed_out']:>12}"
        )
    return "\n".join(lines)


def summarize_saved(out_dir: Path, dataset_path: Path = DEFAULT_DATASET) -> str:
    """Tables, the 3 pre-registered paired comparisons and the rescue statistics, computed from
    the saved result files only."""
    load = lambda name: json.loads((out_dir / name).read_text(encoding="utf-8"))  # noqa: E731
    hybrid = {c: load(HYBRID_FILES[c]) for c in HYBRID_FILES}
    rerank = {c: load(RERANK_FILES[c]) for c in RERANK_FILES}
    primary = {q.id: set(q.primary) for q in load_dataset(dataset_path).questions}

    def cell(r: dict[str, Any]) -> str:
        m = r["results"]["primary"]["overall"]
        return f"{m['top1']:.1%} / {m['topk']:.1%} / {m['mrr']:.3f}"

    parts = ["Overall, primary scope (Top-1 / Top-5 / MRR), 73 questions:"]
    parts[0] += f"\n  {'':<8}{'Hybrid':^26}{'Hybrid + rerank':^26}"
    for c in ("fixed", "ast"):
        parts[0] += f"\n  {c.upper():<8}{cell(hybrid[c]):^26}{cell(rerank[c]):^26}"
    for c in ("fixed", "ast"):
        group = [("HYBRID", hybrid[c]), ("+RERANK", rerank[c])]
        parts.append(f"{c.upper()} chunks, by category and difficulty\n" + format_comparison(group))

    comparisons = [
        ("1. R1 vs H1", hybrid["fixed"], rerank["fixed"], "H1", "R1"),
        ("2. R2 vs H2", hybrid["ast"], rerank["ast"], "H2", "R2"),
        ("3. R2 vs R1", rerank["fixed"], rerank["ast"], "R1", "R2"),
    ]
    lines = ["Pre-registered paired comparisons (B vs A; Bonferroni sign-test threshold 0.0167):"]
    for label, a, b, name_a, name_b in comparisons:
        lines.append(f"\n{label}\n" + format_paired(name_a, name_b, compare_runs(a, b), TOP_K))
    parts.append("\n".join(lines))

    for c, title in (
        ("fixed", "R1 (fixed hybrid -> rerank)"),
        ("ast", "R2 (AST hybrid -> rerank)"),
    ):
        stats = rescue_statistics(rerank[c], primary)
        parts.append(_format_rescue(f"{title}: first primary file, rank before vs after", stats))
    lat = [
        f"{NAMES[c][1]}: first stage median "
        f"{rerank[c]['latency_breakdown_ms']['first_stage']['median_ms']:.1f} ms, reranker median "
        f"{rerank[c]['latency_breakdown_ms']['reranker']['median_ms']:.0f} ms, end-to-end median "
        f"{rerank[c]['results']['latency_ms']['median_ms']:.0f} ms"
        for c in ("fixed", "ast")
    ]
    parts.append("Latency\n  " + "\n  ".join(lat))
    return "\n\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.evaluation.run_rerank", description=__doc__
    )
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
    reranker = CrossEncoderReranker(RERANKER_MODEL, RERANKER_MAX_TOKENS, RERANKER_BATCH_SIZE)

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
            report = _run(chunking, prepared, first_stage, embedder, reranker)
            out = args.out_dir / RERANK_FILES[chunking]
            save_report(report, out)
            print("\n" + format_report(report))
            print(f"Saved to {out}")

    print("\n" + summarize_saved(args.out_dir, args.dataset))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
