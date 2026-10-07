"""Pieces shared by every retrieval experiment, so all methods are measured identically.

An experiment script (baseline, BM25, ...) does: `prepare()` the frozen dataset and checkout,
build a retriever, call `evaluate()`, then `build_report()` / `save_report()` / `format_report()`.
"""

import json
import platform
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings
from app.evaluation.dataset import EvalDataset, load_dataset
from app.evaluation.runner import EvalOutcome, GroupedSummary
from app.evaluation.validate import check_against_repo, check_structure, get_head_commit
from app.ingestion.clone import CloneError
from app.ingestion.service import ingest_repository
from app.models.repository import IngestedRepository

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATASET = REPO_ROOT / "evaluation" / "datasets" / "flask.json"
RESULTS_DIR = REPO_ROOT / "evaluation" / "results"

# Locked scoring parameters (see evaluation/README.md).
TOP_K = 5
RETRIEVAL_DEPTH = 50  # chunks retrieved per question before deduplicating to unique files


class ExperimentError(RuntimeError):
    """The experiment cannot run (bad dataset, wrong checkout, ...)."""


@dataclass
class Prepared:
    dataset: EvalDataset
    ingested: IngestedRepository
    settings: Settings


def prepare(dataset_path: Path = DEFAULT_DATASET) -> Prepared:
    """Load the frozen dataset and make sure the checkout is the one it was verified against."""
    ds = load_dataset(dataset_path)
    problems = check_structure(ds)
    if not ds.frozen:
        problems.append("the dataset is not frozen; evaluate only frozen datasets")
    if problems:
        raise ExperimentError("dataset problems:\n  - " + "\n  - ".join(problems))

    settings = get_settings()
    try:
        ingested = ingest_repository(f"https://github.com/{ds.repository}", settings)
    except CloneError as e:
        raise ExperimentError(str(e)) from e
    head = get_head_commit(ingested.local_path)
    if head != ds.commit:
        raise ExperimentError(
            f"checkout {ingested.local_path} is at {head}, dataset is pinned to {ds.commit}. "
            "See evaluation/README.md."
        )
    problems = check_against_repo(ds, ingested.local_path)
    if problems:
        raise ExperimentError(
            "dataset does not match the checkout:\n  - " + "\n  - ".join(problems)
        )
    return Prepared(ds, ingested, settings)


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), *args], check=True, capture_output=True, text=True
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return out.stdout.strip()


def environment(packages: tuple[str, ...] = ()) -> dict[str, Any]:
    env: dict[str, Any] = {"python": platform.python_version(), "platform": platform.platform()}
    for package in packages:
        env[package.replace("-", "_")] = version(package)
    env["repocompass_commit"] = _git("rev-parse", "HEAD")
    env["repocompass_tree_dirty"] = bool(_git("status", "--porcelain"))
    return env


def build_report(
    ds: EvalDataset,
    outcome: EvalOutcome,
    method: dict[str, Any],
    indexing: dict[str, Any] | None,
    latency_note: str,
    packages: tuple[str, ...] = (),
    index_reused: bool = False,
) -> dict[str, Any]:
    """The saved experiment record. `method` holds everything specific to the retriever."""
    return {
        "dataset": ds.name,
        "dataset_repository": ds.repository,
        "dataset_commit": ds.commit,
        "dataset_fingerprint": ds.questions_sha256,
        "n_questions": len(ds.questions),
        **method,
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
        "latency_note": latency_note,
        "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
        "index_reused": index_reused,
        "indexing_time_seconds": indexing["total_seconds"] if indexing else None,
        "indexing": indexing,
        "environment": environment(packages),
        "results": {
            "primary": outcome.primary.model_dump(),
            "primary_plus_supporting": outcome.primary_plus_supporting.model_dump(),
            "latency_ms": outcome.latency.model_dump(),
        },
        "per_question": [q.model_dump() for q in outcome.questions],
    }


def save_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


# --- console reports -----------------------------------------------------------------


def _row(label: str, a: Any, b: Any) -> str:
    return (
        f"  {label:<12}{a.n:>4}   {a.top1:>6.1%} {a.topk:>6.1%} {a.mrr:>6.3f}"
        f"      {b.top1:>6.1%} {b.topk:>6.1%} {b.mrr:>6.3f}"
    )


def format_report(report: dict[str, Any]) -> str:
    k = report["top_k"]
    p = GroupedSummary.model_validate(report["results"]["primary"])
    b = GroupedSummary.model_validate(report["results"]["primary_plus_supporting"])
    lines = [
        f"{report['retrieval_method']} on {report['dataset']} "
        f"({report['n_questions']} questions, fingerprint {report['dataset_fingerprint'][:10]})",
        f"{report.get('method_description', '')}; depth {report['retrieval_depth_chunks']} "
        f"chunks -> unique files, top-{k}",
        "",
        f"  {'':<12}{'n':>4}   {'PRIMARY (headline)':<22}  PRIMARY + SUPPORTING",
        f"  {'':<12}{'':>4}   {'Top-1':>6} {f'Top-{k}':>6} {'MRR':>6}      "
        f"{'Top-1':>6} {f'Top-{k}':>6} {'MRR':>6}",
        _row("OVERALL", p.overall, b.overall),
        "  by category",
        *[_row(c, p.by_category[c], b.by_category[c]) for c in p.by_category],
        "  by difficulty",
        *[_row(d, p.by_difficulty[d], b.by_difficulty[d]) for d in p.by_difficulty],
        "",
    ]
    lat = report["results"]["latency_ms"]
    lines.append(
        f"Query latency: mean {lat['mean_ms']:.2f} ms, median {lat['median_ms']:.2f} ms, "
        f"p95 {lat['p95_ms']:.2f} ms, max {lat['max_ms']:.2f} ms"
    )
    ix = report.get("indexing")
    if ix:
        extras = ", ".join(
            f"{key.removesuffix('_seconds')} {value:.2f}s"
            for key, value in ix.items()
            if key.endswith("_seconds") and key != "total_seconds"
        )
        lines.append(
            f"Indexing: {ix['total_seconds']:.2f}s total "
            f"({ix['files']} files -> {ix['chunks']} chunks; {extras})"
        )
    return "\n".join(lines)


def format_comparison(reports: list[tuple[str, dict[str, Any]]]) -> str:
    """Primary-scope results of several methods side by side (Top-1 / Top-5 / MRR)."""
    k = reports[0][1]["top_k"]
    width = 21
    head = "  " + " " * 13 + "".join(f"{name:^{width}}" for name, _ in reports)
    sub = "  " + " " * 13 + "".join(f"{'Top-1':>7}{f'Top-{k}':>7}{'MRR':>7}" for _ in reports)
    groups = [GroupedSummary.model_validate(r["results"]["primary"]) for _, r in reports]

    def row(label: str, pick: Any) -> str:
        cells = ""
        for g in groups:
            m = pick(g)
            cells += f"{m.top1:>7.1%}{m.topk:>7.1%}{m.mrr:>7.3f}"
        return f"  {label:<13}" + cells

    lines = ["PRIMARY scope, same 73 questions:", head, sub, row("OVERALL", lambda g: g.overall)]
    for section, attr in (("category", "by_category"), ("difficulty", "by_difficulty")):
        lines.append(f"  by {section}")
        for key in getattr(groups[0], attr):
            lines.append(row(key, lambda g, a=attr, key=key: getattr(g, a)[key]))
    latencies = [f"{name} {r['results']['latency_ms']['median_ms']:.2f} ms" for name, r in reports]
    lines.append("  median query latency: " + "; ".join(latencies))
    return "\n".join(lines)
