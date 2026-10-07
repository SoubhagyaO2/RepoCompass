"""Evaluate the fixed-chunk embedding baseline on a frozen dataset and save the results.

    python -m app.evaluation.run_baseline [--dataset PATH] [--out PATH] [--skip-index]

By default this re-indexes the repository (so indexing time is measured), runs one warm-up
query, evaluates every question once, and writes a JSON file with the full experiment metadata.
"""

import argparse
import sys
from pathlib import Path

from app.evaluation.experiment import (
    DEFAULT_DATASET,
    RESULTS_DIR,
    RETRIEVAL_DEPTH,
    TOP_K,
    ExperimentError,
    build_report,
    format_report,
    prepare,
    save_report,
)
from app.evaluation.runner import evaluate
from app.retrieval.baseline import BaselineIndexStats, FixedChunkBaseline
from app.retrieval.embedder import get_embedder
from app.retrieval.vector_store import VectorStore

DEFAULT_OUT = RESULTS_DIR / "flask_fixed_chunk_baseline.json"

__all__ = ["RETRIEVAL_DEPTH", "TOP_K", "main"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.evaluation.run_baseline", description=__doc__
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--skip-index", action="store_true", help="reuse the existing index (no timing recorded)"
    )
    args = parser.parse_args(argv)

    try:
        prepared = prepare(args.dataset)
    except ExperimentError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    ds, settings = prepared.dataset, prepared.settings

    embedder = get_embedder(settings)
    with VectorStore(settings.qdrant_path) as store:
        baseline = FixedChunkBaseline(
            embedder,
            store,
            settings.baseline_chunk_size,
            settings.baseline_chunk_overlap,
            show_progress=True,
        )
        index_stats: BaselineIndexStats | None = None
        if args.skip_index:
            if not baseline.is_indexed(ds.repository):
                print("error: --skip-index but the repository is not indexed", file=sys.stderr)
                return 1
        else:
            index_stats = baseline.index(prepared.ingested)

        baseline.search(ds.repository, "warm-up query", 1)  # untimed: first call is slower
        outcome = evaluate(
            ds,
            lambda query, depth: baseline.search(ds.repository, query, depth),
            RETRIEVAL_DEPTH,
            TOP_K,
        )

    report = build_report(
        ds,
        outcome,
        method={
            "retrieval_method": "fixed_chunk_baseline",
            "method_description": f"fixed chunks {settings.baseline_chunk_size}/"
            f"{settings.baseline_chunk_overlap} overlap + embeddings "
            f"({embedder.model_name}, {embedder.device})",
            "chunk_size": settings.baseline_chunk_size,
            "chunk_overlap": settings.baseline_chunk_overlap,
            "embedding_model": embedder.model_name,
            "embedding_query_prefix": settings.embedding_query_prefix,
            "device": embedder.device,
        },
        indexing=index_stats.model_dump() if index_stats else None,
        latency_note="per query: embed the question + vector search, model already loaded, "
        "after one untimed warm-up query",
        packages=("sentence-transformers", "torch", "qdrant-client"),
        index_reused=args.skip_index,
    )
    save_report(report, args.out)
    print("\n" + format_report(report))
    print(f"\nSaved to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
