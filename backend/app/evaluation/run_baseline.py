"""Evaluate the fixed-chunk baseline on a frozen dataset and save the results.

    python -m app.evaluation.run_baseline [--dataset PATH] [--out PATH] [--skip-index]

By default this re-indexes the repository (so indexing time is measured), runs one warm-up
query, evaluates every question once, and writes a JSON file with the full experiment metadata.
"""

import argparse
import json
import platform
import subprocess
import sys
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.evaluation.dataset import EvalDataset, load_dataset
from app.evaluation.runner import EvalOutcome, GroupedSummary, evaluate
from app.evaluation.validate import check_against_repo, check_structure, get_head_commit
from app.ingestion.clone import CloneError
from app.ingestion.service import ingest_repository
from app.retrieval.baseline import BaselineIndexStats, FixedChunkBaseline
from app.retrieval.embedder import get_embedder
from app.retrieval.vector_store import VectorStore

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATASET = REPO_ROOT / "evaluation" / "datasets" / "flask.json"
DEFAULT_OUT = REPO_ROOT / "evaluation" / "results" / "flask_fixed_chunk_baseline.json"

TOP_K = 5
RETRIEVAL_DEPTH = 50  # chunks retrieved per question before deduplicating to unique files


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), *args], check=True, capture_output=True, text=True
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return out.stdout.strip()


def _environment() -> dict[str, Any]:
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "sentence_transformers": version("sentence-transformers"),
        "torch": version("torch"),
        "qdrant_client": version("qdrant-client"),
        "repocompass_commit": _git("rev-parse", "HEAD"),
        "repocompass_tree_dirty": bool(_git("status", "--porcelain")),
    }


def build_report(
    ds: EvalDataset,
    outcome: EvalOutcome,
    index_stats: BaselineIndexStats | None,
    embedder_info: dict[str, str],
    index_reused: bool,
) -> dict[str, Any]:
    settings = get_settings()
    return {
        "dataset": ds.name,
        "dataset_repository": ds.repository,
        "dataset_commit": ds.commit,
        "dataset_fingerprint": ds.questions_sha256,
        "n_questions": len(ds.questions),
        "retrieval_method": "fixed_chunk_baseline",
        "chunk_size": settings.baseline_chunk_size,
        "chunk_overlap": settings.baseline_chunk_overlap,
        "embedding_model": embedder_info["model"],
        "embedding_query_prefix": settings.embedding_query_prefix,
        "device": embedder_info["device"],
        "top_k": TOP_K,
        "retrieval_depth_chunks": RETRIEVAL_DEPTH,
        "deduplication": "first_file_occurrence",
        "scoring": "file_level",
        "hit_definition": "at least one counted file is among the first top_k unique files",
        "mrr_definition": "mean 1/rank of the first counted file in the unique-file ranking "
        "(0 if none was retrieved); does not require every required file",
        "scopes": {
            "primary": "counted files = primary (headline)",
            "primary_plus_supporting": "counted files = primary + supporting",
        },
        "latency_note": "per query: embed the question + vector search, model already loaded, "
        "after one untimed warm-up query",
        "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
        "index_reused": index_reused,
        "indexing_time_seconds": index_stats.total_seconds if index_stats else None,
        "indexing": index_stats.model_dump() if index_stats else None,
        "environment": _environment(),
        "results": {
            "primary": outcome.primary.model_dump(),
            "primary_plus_supporting": outcome.primary_plus_supporting.model_dump(),
            "latency_ms": outcome.latency.model_dump(),
        },
        "per_question": [q.model_dump() for q in outcome.questions],
    }


# --- console report ------------------------------------------------------------------


def _row(label: str, a: Any, b: Any, k: int) -> str:
    return (
        f"  {label:<12}{a.n:>4}   {a.top1:>6.1%} {a.topk:>6.1%} {a.mrr:>6.3f}"
        f"      {b.top1:>6.1%} {b.topk:>6.1%} {b.mrr:>6.3f}"
    )


def format_report(report: dict[str, Any]) -> str:
    k = report["top_k"]
    prim: dict[str, Any] = report["results"]["primary"]
    both: dict[str, Any] = report["results"]["primary_plus_supporting"]
    p, b = GroupedSummary.model_validate(prim), GroupedSummary.model_validate(both)
    lines = [
        f"{report['retrieval_method']} on {report['dataset']} "
        f"({report['n_questions']} questions, fingerprint {report['dataset_fingerprint'][:10]})",
        f"chunks {report['chunk_size']}/{report['chunk_overlap']} overlap, "
        f"{report['embedding_model']} on {report['device']}, "
        f"depth {report['retrieval_depth_chunks']} chunks -> unique files, top-{k}",
        "",
        f"  {'':<12}{'n':>4}   {'PRIMARY (headline)':<22}  PRIMARY + SUPPORTING",
        f"  {'':<12}{'':>4}   {'Top-1':>6} {f'Top-{k}':>6} {'MRR':>6}      "
        f"{'Top-1':>6} {f'Top-{k}':>6} {'MRR':>6}",
        _row("OVERALL", p.overall, b.overall, k),
        "  by category",
        *[_row(c, p.by_category[c], b.by_category[c], k) for c in p.by_category],
        "  by difficulty",
        *[_row(d, p.by_difficulty[d], b.by_difficulty[d], k) for d in p.by_difficulty],
        "",
    ]
    lat = report["results"]["latency_ms"]
    lines.append(
        f"Query latency: mean {lat['mean_ms']:.0f} ms, median {lat['median_ms']:.0f} ms, "
        f"p95 {lat['p95_ms']:.0f} ms, max {lat['max_ms']:.0f} ms"
    )
    if report["indexing"]:
        ix = report["indexing"]
        lines.append(
            f"Indexing: {report['indexing_time_seconds']:.1f}s total "
            f"({ix['files']} files -> {ix['chunks']} chunks; "
            f"embedding {ix['embedding_seconds']:.1f}s)"
        )
    return "\n".join(lines)


# --- main ----------------------------------------------------------------------------


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

    ds = load_dataset(args.dataset)
    problems = check_structure(ds)
    if not ds.frozen:
        problems.append("the dataset is not frozen; evaluate only frozen datasets")
    if problems:
        print("dataset problems:\n  - " + "\n  - ".join(problems), file=sys.stderr)
        return 1

    settings = get_settings()
    try:
        ingested = ingest_repository(f"https://github.com/{ds.repository}", settings)
    except CloneError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    head = get_head_commit(ingested.local_path)
    if head != ds.commit:
        print(
            f"error: checkout {ingested.local_path} is at {head}, dataset is pinned to "
            f"{ds.commit}. See evaluation/README.md.",
            file=sys.stderr,
        )
        return 1
    problems = check_against_repo(ds, ingested.local_path)
    if problems:
        print(
            "dataset does not match the checkout:\n  - " + "\n  - ".join(problems), file=sys.stderr
        )
        return 1

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
            index_stats = baseline.index(ingested)

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
        index_stats,
        {"model": embedder.model_name, "device": embedder.device},
        index_reused=args.skip_index,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("\n" + format_report(report))
    print(f"\nSaved to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
