"""Evaluate BM25 on fixed-size chunks and on AST-aware chunks, with the same frozen dataset,
scoring, breakdowns and latency measurement as the embedding baseline.

    python -m app.evaluation.run_bm25 [--only fixed|ast]

Each method writes evaluation/results/flask_<fixed|ast>_chunk_bm25.json. The BM25 configuration
(k1, b, tokenizer, query stopwords) was fixed before the benchmark was run and is not tuned.
"""

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

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
from app.evaluation.runner import Retriever, evaluate
from app.retrieval.bm25 import K1, QUERY_STOPWORDS, B
from app.retrieval.chunk_bm25 import (
    Bm25BuildStats,
    Bm25ChunkRetriever,
    build_ast_bm25,
    build_fixed_bm25,
)

BASELINE_RESULT = RESULTS_DIR / "flask_fixed_chunk_baseline.json"
LATENCY_NOTE = (
    "per query: tokenize the question + BM25 scoring over the in-memory index, "
    "after one untimed warm-up query"
)


def _method_fields(chunking: str, prepared: Prepared, stats: Bm25BuildStats) -> dict[str, Any]:
    settings = prepared.settings
    fields: dict[str, Any] = {
        "retrieval_method": f"{chunking}_chunk_bm25",
        "chunking": chunking,
        "bm25_k1": K1,
        "bm25_b": B,
        "tokenizer": "lowercase; identifiers indexed whole and split on '_' and camelCase; "
        "no stemming",
        "query_stopwords": sorted(QUERY_STOPWORDS),
        "indexed_text": "chunk content only (no file path or symbol name)",
        "tuned_on_benchmark": False,
        "chunks": stats.chunks,
    }
    if chunking == "fixed":
        fields["chunk_size"] = settings.baseline_chunk_size
        fields["chunk_overlap"] = settings.baseline_chunk_overlap
        fields["method_description"] = (
            f"fixed chunks {settings.baseline_chunk_size}/{settings.baseline_chunk_overlap} "
            f"overlap + BM25 (k1={K1}, b={B})"
        )
    else:
        fields["method_description"] = f"AST-aware chunks + BM25 (k1={K1}, b={B})"
    return fields


def run_method(chunking: str, prepared: Prepared, builder: Callable[[], Any]) -> dict[str, Any]:
    retriever: Bm25ChunkRetriever
    retriever, stats = builder()
    search: Retriever = lambda query, depth: retriever.search(query, depth)  # noqa: E731
    search("warm-up query", 1)  # untimed, as in the embedding baseline
    outcome = evaluate(prepared.dataset, search, RETRIEVAL_DEPTH, TOP_K)
    return build_report(
        prepared.dataset,
        outcome,
        method=_method_fields(chunking, prepared, stats),
        indexing=stats.model_dump(),
        latency_note=LATENCY_NOTE,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.evaluation.run_bm25", description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--out-dir", type=Path, default=RESULTS_DIR)
    parser.add_argument("--only", choices=["fixed", "ast"], help="run just one chunking")
    args = parser.parse_args(argv)

    try:
        prepared = prepare(args.dataset)
    except ExperimentError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    settings = prepared.settings

    builders: dict[str, Callable[[], Any]] = {
        "fixed": lambda: build_fixed_bm25(
            prepared.ingested, settings.baseline_chunk_size, settings.baseline_chunk_overlap
        ),
        "ast": lambda: build_ast_bm25(prepared.ingested),
    }
    reports: list[tuple[str, dict[str, Any]]] = []
    for chunking in [args.only] if args.only else ["fixed", "ast"]:
        report = run_method(chunking, prepared, builders[chunking])
        out = args.out_dir / f"flask_{chunking}_chunk_bm25.json"
        save_report(report, out)
        print("\n" + format_report(report))
        print(f"Saved to {out}")
        reports.append((f"{chunking.upper()[:5]}+BM25", report))

    if BASELINE_RESULT.exists():
        baseline = json.loads(BASELINE_RESULT.read_text(encoding="utf-8"))
        reports.insert(0, ("FIXED+EMB", baseline))
    if len(reports) > 1:
        print("\n" + format_comparison(reports))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
