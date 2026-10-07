"""Run any retrieval strategy over an evaluation dataset and score it.

A strategy is just a function `(query, depth) -> ranked SearchHits`, so the fixed-chunk baseline,
BM25, hybrid, ... are all evaluated by exactly the same code.
"""

import time
from collections.abc import Callable

from pydantic import BaseModel

from app.evaluation.dataset import EvalDataset, EvalQuestion
from app.evaluation.scoring import (
    MetricSummary,
    ScopeResult,
    score_ranking,
    summarize,
    unique_files,
)
from app.models.search import SearchHit

Retriever = Callable[[str, int], list[SearchHit]]

CATEGORY_ORDER = ("definition", "caller", "dependency", "concept", "flow")
DIFFICULTY_ORDER = ("easy", "medium", "hard")
RECORDED_FILES = 10  # how many unique retrieved files to keep per question, for later analysis


class QuestionResult(BaseModel):
    id: str
    question: str
    category: str
    difficulty: str
    retrieved_files: list[str]  # first RECORDED_FILES unique files, best first
    primary: ScopeResult
    primary_plus_supporting: ScopeResult
    latency_ms: float


class GroupedSummary(BaseModel):
    overall: MetricSummary
    by_category: dict[str, MetricSummary]
    by_difficulty: dict[str, MetricSummary]


class LatencyStats(BaseModel):
    mean_ms: float
    median_ms: float
    p95_ms: float
    max_ms: float


class EvalOutcome(BaseModel):
    questions: list[QuestionResult]
    primary: GroupedSummary
    primary_plus_supporting: GroupedSummary
    latency: LatencyStats


def evaluate(dataset: EvalDataset, retrieve: Retriever, depth: int = 50, k: int = 5) -> EvalOutcome:
    """Retrieve `depth` chunks per question, dedupe to unique files, and score at `k`."""
    results = [_run_question(q, retrieve, depth, k) for q in dataset.questions]
    return EvalOutcome(
        questions=results,
        primary=_group(results, lambda r: r.primary),
        primary_plus_supporting=_group(results, lambda r: r.primary_plus_supporting),
        latency=_latency([r.latency_ms for r in results]),
    )


def _run_question(q: EvalQuestion, retrieve: Retriever, depth: int, k: int) -> QuestionResult:
    start = time.perf_counter()
    hits = retrieve(q.question, depth)
    latency_ms = (time.perf_counter() - start) * 1000

    files = unique_files(h.file_path for h in hits)
    return QuestionResult(
        id=q.id,
        question=q.question,
        category=q.category,
        difficulty=q.difficulty,
        retrieved_files=files[:RECORDED_FILES],
        primary=score_ranking(files, set(q.primary), k),
        primary_plus_supporting=score_ranking(files, set(q.relevant_files), k),
        latency_ms=latency_ms,
    )


def _group(
    results: list[QuestionResult], scope: Callable[[QuestionResult], ScopeResult]
) -> GroupedSummary:
    def subset(attr: str, value: str) -> list[ScopeResult]:
        return [scope(r) for r in results if getattr(r, attr) == value]

    return GroupedSummary(
        overall=summarize(scope(r) for r in results),
        by_category={c: summarize(subset("category", c)) for c in CATEGORY_ORDER},
        by_difficulty={d: summarize(subset("difficulty", d)) for d in DIFFICULTY_ORDER},
    )


def _latency(values: list[float]) -> LatencyStats:
    if not values:
        return LatencyStats(mean_ms=0.0, median_ms=0.0, p95_ms=0.0, max_ms=0.0)
    ordered = sorted(values)
    return LatencyStats(
        mean_ms=sum(ordered) / len(ordered),
        median_ms=ordered[len(ordered) // 2],
        p95_ms=ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))],
        max_ms=ordered[-1],
    )
