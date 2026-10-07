"""Milestone 8: hybrid retrieval (BM25 + embeddings, reciprocal rank fusion) on both chunkings,
plus the AST + embeddings control.

    python -m app.evaluation.run_hybrid

Runs, on the same frozen dataset with the same locked scoring as every other method:
  * ast_chunk_embeddings : control, so the AST hybrid can be interpreted
  * fixed_chunk_hybrid   : H1, fixed chunks, BM25 + embeddings fused with RRF
  * ast_chunk_hybrid     : H2, AST chunks,   BM25 + embeddings fused with RRF

Pre-registered before any hybrid result existed (and never tuned on the benchmark): RRF with
k = 60, equal weights, fused over unique files, each component retrieving 50 chunks; the six
paired comparisons in PREREGISTERED_COMPARISONS.
"""

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.chunking.fixed_chunker import chunk_repository
from app.evaluation.compare import compare_runs
from app.evaluation.compare import format_comparison as format_paired
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
from app.evaluation.runner import evaluate
from app.indexing.service import build_index
from app.models.search import SearchHit
from app.retrieval.baseline import FixedChunkBaseline
from app.retrieval.bm25 import K1, B
from app.retrieval.chunk_bm25 import build_ast_bm25, build_fixed_bm25
from app.retrieval.chunk_embeddings import build_ast_embeddings
from app.retrieval.embedder import Embedder, get_embedder
from app.retrieval.hybrid import RRF_K, HybridRetriever
from app.retrieval.vector_store import VectorStore

SearchFn = Callable[[str, int], list[SearchHit]]

FILES = {
    ("fixed", "embeddings"): "flask_fixed_chunk_baseline.json",
    ("fixed", "bm25"): "flask_fixed_chunk_bm25.json",
    ("fixed", "hybrid"): "flask_fixed_chunk_hybrid.json",
    ("ast", "embeddings"): "flask_ast_chunk_embeddings.json",
    ("ast", "bm25"): "flask_ast_chunk_bm25.json",
    ("ast", "hybrid"): "flask_ast_chunk_hybrid.json",
}

# (label, reference run A, run B): B is compared against A. Fixed before the results existed.
PREREGISTERED_COMPARISONS: list[tuple[str, tuple[str, str], tuple[str, str]]] = [
    ("1. H1 vs fixed+embeddings", ("fixed", "embeddings"), ("fixed", "hybrid")),
    ("2. H1 vs fixed+BM25", ("fixed", "bm25"), ("fixed", "hybrid")),
    ("3. H2 vs AST+embeddings", ("ast", "embeddings"), ("ast", "hybrid")),
    ("4. H2 vs AST+BM25", ("ast", "bm25"), ("ast", "hybrid")),
    ("5. AST+embeddings vs fixed+embeddings", ("fixed", "embeddings"), ("ast", "embeddings")),
    ("6. H2 vs H1", ("fixed", "hybrid"), ("ast", "hybrid")),
]

LATENCY_EMBEDDINGS = (
    "per query: embed the question + vector search, model already loaded, "
    "after one untimed warm-up query"
)
LATENCY_HYBRID = (
    "per query: BM25 search + embed the question and vector search + file-level fusion, "
    "model already loaded, after one untimed warm-up query"
)


def _truncation(embedder: Embedder, contents: list[str]) -> dict[str, Any]:
    """How many chunks are longer than the embedding model can read."""
    limit = getattr(embedder, "max_seq_length", None)
    counter = getattr(embedder, "count_tokens", None)
    if not limit or counter is None:
        return {}
    over = sum(1 for n in counter(contents) if n > limit)
    return {"embedder_max_tokens": limit, "chunks_over_embedder_limit": over}


def _embedding_fields(embedder: Embedder, prepared: Prepared) -> dict[str, Any]:
    return {
        "embedding_model": embedder.model_name,
        "embedding_query_prefix": prepared.settings.embedding_query_prefix,
        "device": embedder.device,
        "embedded_text": "chunk content only (no file path or symbol name)",
    }


def _bm25_fields() -> dict[str, Any]:
    return {"bm25_k1": K1, "bm25_b": B, "bm25_indexed_text": "chunk content only"}


def _fusion_fields() -> dict[str, Any]:
    return {
        "fusion": {
            "method": "reciprocal_rank_fusion",
            "rrf_k": RRF_K,
            "weights": "equal",
            "fused_at": "unique files: each component's chunks are deduplicated to files "
            "(first occurrence) before fusion, so a file gets one vote per component",
            "component_depth_chunks": RETRIEVAL_DEPTH,
            "tie_break": "best individual rank, then file path",
            "components": ["bm25", "embeddings"],
        },
        "tuned_on_benchmark": False,
    }


def _matrix(reports: dict[tuple[str, str], dict[str, Any]]) -> str:
    def cell(key: tuple[str, str]) -> str:
        m = reports[key]["results"]["primary"]["overall"]
        return f"{m['top1']:.1%} / {m['topk']:.1%} / {m['mrr']:.3f}"

    lines = ["Overall, primary scope (Top-1 / Top-5 / MRR), 73 questions:"]
    lines.append(f"  {'':<8}{'Embeddings':^26}{'BM25':^26}{'Hybrid (RRF)':^26}")
    for chunking in ("fixed", "ast"):
        cells = "".join(f"{cell((chunking, r)):^26}" for r in ("embeddings", "bm25", "hybrid"))
        lines.append(f"  {chunking.upper():<8}{cells}")
    return "\n".join(lines)


def summarize_saved(out_dir: Path) -> str:
    """The matrix, per-category tables and the pre-registered paired comparisons, computed
    from the saved result files only (so it can be re-printed without re-running anything)."""
    reports = {
        key: json.loads((out_dir / name).read_text(encoding="utf-8")) for key, name in FILES.items()
    }
    parts = [_matrix(reports)]
    for chunking in ("fixed", "ast"):
        group = [
            (label, reports[(chunking, kind)])
            for label, kind in (("EMB", "embeddings"), ("BM25", "bm25"), ("HYBRID", "hybrid"))
        ]
        parts.append(
            f"{chunking.upper()} chunks, by category and difficulty\n" + format_comparison(group)
        )

    lines = ["Pre-registered paired comparisons (B vs A; uncorrected for 6 comparisons):"]
    for label, a, b in PREREGISTERED_COMPARISONS:
        c = compare_runs(reports[a], reports[b])
        lines.append(f"\n{label}\n" + format_paired("+".join(a), "+".join(b), c, TOP_K))
    parts.append("\n".join(lines))
    return "\n\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.evaluation.run_hybrid", description=__doc__
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--out-dir", type=Path, default=RESULTS_DIR)
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="print the summary and paired comparisons from the saved results; run nothing",
    )
    args = parser.parse_args(argv)
    if args.report_only:
        print(summarize_saved(args.out_dir))
        return 0

    try:
        prepared = prepare(args.dataset)
    except ExperimentError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    ds, settings, ingested = prepared.dataset, prepared.settings, prepared.ingested
    repo = ds.repository
    size, overlap = settings.baseline_chunk_size, settings.baseline_chunk_overlap
    embedder = get_embedder(settings)

    with VectorStore(settings.qdrant_path) as store:
        # Build all four indexes, timing each.
        fixed_emb = FixedChunkBaseline(embedder, store, size, overlap, show_progress=True)
        fixed_emb_stats = fixed_emb.index(ingested)
        fixed_bm25, fixed_bm25_stats = build_fixed_bm25(ingested, size, overlap)
        ast_emb, ast_emb_stats = build_ast_embeddings(ingested, embedder, store, show_progress=True)
        ast_bm25, ast_bm25_stats = build_ast_bm25(ingested)

        fixed_contents = [c.content for c in chunk_repository(ingested, size, overlap)[0]]
        ast_contents = [c.content for c in build_index(ingested)[0]]
        fixed_trunc = _truncation(embedder, fixed_contents)
        ast_trunc = _truncation(embedder, ast_contents)

        fixed_emb_search: SearchFn = lambda q, d: fixed_emb.search(repo, q, d)  # noqa: E731
        experiments: list[tuple[str, str, SearchFn, dict[str, Any], dict[str, Any], str]] = [
            (
                "ast",
                "embeddings",
                ast_emb.search,
                {
                    "retrieval_method": "ast_chunk_embeddings",
                    "method_description": f"AST-aware chunks + embeddings ({embedder.model_name}, "
                    f"{embedder.device}); control for the AST hybrid",
                    "chunking": "ast",
                    **_embedding_fields(embedder, prepared),
                    **ast_trunc,
                },
                ast_emb_stats.model_dump(),
                LATENCY_EMBEDDINGS,
            ),
            (
                "fixed",
                "hybrid",
                HybridRetriever([fixed_bm25.search, fixed_emb_search]).search,
                {
                    "retrieval_method": "fixed_chunk_hybrid",
                    "method_description": f"H1: fixed chunks {size}/{overlap} + BM25 + embeddings, "
                    f"RRF k={RRF_K}",
                    "chunking": "fixed",
                    "chunk_size": size,
                    "chunk_overlap": overlap,
                    **_embedding_fields(embedder, prepared),
                    **_bm25_fields(),
                    **_fusion_fields(),
                    **fixed_trunc,
                },
                {
                    "files": fixed_emb_stats.files,
                    "chunks": fixed_emb_stats.chunks,
                    "embedding_index_seconds": fixed_emb_stats.total_seconds,
                    "bm25_index_seconds": fixed_bm25_stats.total_seconds,
                    "total_seconds": fixed_emb_stats.total_seconds + fixed_bm25_stats.total_seconds,
                },
                LATENCY_HYBRID,
            ),
            (
                "ast",
                "hybrid",
                HybridRetriever([ast_bm25.search, ast_emb.search]).search,
                {
                    "retrieval_method": "ast_chunk_hybrid",
                    "method_description": f"H2: AST chunks + BM25 + embeddings, RRF k={RRF_K}",
                    "chunking": "ast",
                    **_embedding_fields(embedder, prepared),
                    **_bm25_fields(),
                    **_fusion_fields(),
                    **ast_trunc,
                },
                {
                    "files": ast_emb_stats.files,
                    "chunks": ast_emb_stats.chunks,
                    "embedding_index_seconds": ast_emb_stats.total_seconds,
                    "bm25_index_seconds": ast_bm25_stats.total_seconds,
                    "total_seconds": ast_emb_stats.total_seconds + ast_bm25_stats.total_seconds,
                },
                LATENCY_HYBRID,
            ),
        ]

        for chunking, kind, search, method, indexing, latency_note in experiments:
            search("warm-up query", 1)  # untimed, as for every other method
            outcome = evaluate(ds, search, RETRIEVAL_DEPTH, TOP_K)
            report = build_report(
                ds,
                outcome,
                method=method,
                indexing=indexing,
                latency_note=latency_note,
                packages=("sentence-transformers", "torch", "qdrant-client"),
            )
            out = args.out_dir / FILES[(chunking, kind)]
            save_report(report, out)
            print("\n" + format_report(report))
            print(f"Saved to {out}")

    print("\n" + summarize_saved(args.out_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
