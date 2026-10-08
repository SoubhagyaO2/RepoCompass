"""What the code graph itself answers for the benchmark's caller and dependency questions.

Descriptive information only (pre-registered in the M10 plan): computed once after the graph was
frozen, never used to change it. For the dependency category the comparison is circular, because
the benchmark's answers were derived from the same parser; for callers it is partly independent
(the benchmark's answers come from a text search of identifiers, which also counts references that
are not calls).
"""

import re
from typing import Any

from app.evaluation.dataset import EvalQuestion
from app.graph.model import CodeGraph

SCOPE = "src/flask/"  # the benchmark only ever counts library files
_BACKTICK = re.compile(r"`([^`]+)`")


def graph_answer(graph: CodeGraph, q: EvalQuestion) -> set[str] | None:
    """The files the graph yields for a caller or dependency question; None if not applicable."""
    tokens = _BACKTICK.findall(q.question)
    if not tokens or q.category not in ("caller", "dependency"):
        return None
    token = tokens[0]

    if q.category == "caller":
        files: set[str] = set()
        for symbol in graph.definitions(token):
            if symbol.startswith(SCOPE):
                files |= {graph.file_of(e.source) for e in graph.callers_of(symbol)}
        return {f for f in files if f.startswith(SCOPE)}

    path = "src/" + token if token.endswith(".py") else "src/" + token.replace(".", "/") + ".py"
    forward = " does " in q.question and ("import" in q.question or "depend" in q.question)
    edges = graph.imports_of(path) if forward else graph.importers_of(path)
    ends = {e.target if forward else e.source for e in edges}
    return {f for f in ends if f.startswith(SCOPE)}


def compare_with_benchmark(graph: CodeGraph, questions: list[EvalQuestion]) -> dict[str, Any]:
    """Precision / recall of the graph's answer against the benchmark's primary files."""
    rows: list[dict[str, Any]] = []
    for q in questions:
        answer = graph_answer(graph, q)
        if answer is None:
            continue
        truth = set(q.primary)
        hit = answer & truth
        rows.append(
            {
                "id": q.id,
                "category": q.category,
                "question": q.question,
                "graph_files": sorted(answer),
                "benchmark_files": sorted(truth),
                "precision": len(hit) / len(answer) if answer else 0.0,
                "recall": len(hit) / len(truth),
                "exact": answer == truth,
                "missing": sorted(truth - answer),
                "extra": sorted(answer - truth),
            }
        )

    summary: dict[str, Any] = {}
    for category in ("caller", "dependency"):
        group = [r for r in rows if r["category"] == category]
        if group:
            summary[category] = {
                "n": len(group),
                "exact_match": sum(r["exact"] for r in group),
                "mean_precision": sum(r["precision"] for r in group) / len(group),
                "mean_recall": sum(r["recall"] for r in group) / len(group),
            }
    return {"summary": summary, "per_question": rows}
