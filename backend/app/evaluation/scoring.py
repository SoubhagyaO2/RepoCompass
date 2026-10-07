"""File-level retrieval scoring. This methodology is locked; see evaluation/README.md.

For each question:
  1. take the ranked list of retrieved chunks and map each to its file path;
  2. drop repeated files, keeping each file's first occurrence;
  3. score the resulting ranked list of *unique files* against the ground-truth files.

A question is a hit at k if at least one ground-truth file is among the first k unique files.
Reciprocal rank is 1 / (rank of the first ground-truth file), so MRR measures the first relevant
file; it does not require every required file to be found.
"""

from collections.abc import Collection, Iterable

from pydantic import BaseModel


class ScopeResult(BaseModel):
    """How one question scored against one set of ground-truth files."""

    first_relevant_rank: int | None  # 1-based rank among unique files; None if absent
    hit_at_1: bool
    hit_at_k: bool
    reciprocal_rank: float


class MetricSummary(BaseModel):
    n: int
    top1: float  # fraction of questions that are a hit at 1
    topk: float  # fraction of questions that are a hit at k
    mrr: float  # mean reciprocal rank of the first relevant file


def unique_files(paths: Iterable[str]) -> list[str]:
    """Remove duplicate paths, keeping the first occurrence of each, in rank order."""
    seen: set[str] = set()
    ordered: list[str] = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            ordered.append(path)
    return ordered


def score_ranking(ranked_files: list[str], relevant: Collection[str], k: int) -> ScopeResult:
    """Score a ranked list of unique files. `relevant` is the set of files that count."""
    rank = next((i for i, f in enumerate(ranked_files, 1) if f in relevant), None)
    return ScopeResult(
        first_relevant_rank=rank,
        hit_at_1=rank == 1,
        hit_at_k=rank is not None and rank <= k,
        reciprocal_rank=1 / rank if rank else 0.0,
    )


def summarize(results: Iterable[ScopeResult]) -> MetricSummary:
    rs = list(results)
    if not rs:
        return MetricSummary(n=0, top1=0.0, topk=0.0, mrr=0.0)
    n = len(rs)
    return MetricSummary(
        n=n,
        top1=sum(r.hit_at_1 for r in rs) / n,
        topk=sum(r.hit_at_k for r in rs) / n,
        mrr=sum(r.reciprocal_rank for r in rs) / n,
    )
