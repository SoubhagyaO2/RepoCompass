"""Paired comparison of two saved runs on the same questions.

    python -m app.evaluation.compare RESULT_A.json RESULT_B.json [--scope primary]

With 73 questions a few points of Top-1 are a handful of questions, so differences between
methods need an uncertainty estimate. This reports, for B relative to A:

* wins / losses / ties: questions where B ranks the first counted file higher / lower / equal;
* an exact two-sided sign test on the wins vs losses (ties ignored);
* the mean differences in Top-1, Top-5 and MRR, with a 95% paired-bootstrap interval for MRR
  (10,000 resamples of the questions, fixed seed).

It is descriptive: it never changes a score, and the methodology is fixed in advance.
"""

import argparse
import json
import random
from math import comb
from pathlib import Path
from typing import Any

from pydantic import BaseModel

BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_SEED = 0


class PairedComparison(BaseModel):
    n: int
    wins: int  # B ranks the first counted file higher than A
    losses: int
    ties: int
    sign_test_p: float  # two-sided, exact, ties ignored
    top1_diff: float  # B - A, as fractions
    topk_diff: float
    mrr_diff: float
    mrr_diff_ci95: tuple[float, float]


def sign_test_p(wins: int, losses: int) -> float:
    """Exact two-sided binomial sign test (H0: wins and losses are equally likely)."""
    n = wins + losses
    if n == 0:
        return 1.0
    tail = sum(comb(n, i) for i in range(min(wins, losses) + 1)) / 2**n
    return min(1.0, 2 * tail)


def compare_runs(a: dict[str, Any], b: dict[str, Any], scope: str = "primary") -> PairedComparison:
    qa, qb = a["per_question"], b["per_question"]
    if [q["id"] for q in qa] != [q["id"] for q in qb]:
        raise ValueError("the two runs are not over the same questions")
    k = a["top_k"]
    if k != b["top_k"]:
        raise ValueError("the two runs use different top_k")

    wins = losses = ties = 0
    diffs_rr: list[float] = []
    top1: list[float] = []
    topk: list[float] = []
    for x, y in zip(qa, qb, strict=True):
        sx, sy = x[scope], y[scope]
        rank_x = sx["first_relevant_rank"] or float("inf")
        rank_y = sy["first_relevant_rank"] or float("inf")
        wins += rank_y < rank_x
        losses += rank_y > rank_x
        ties += rank_y == rank_x
        diffs_rr.append(sy["reciprocal_rank"] - sx["reciprocal_rank"])
        top1.append(float(sy["hit_at_1"]) - float(sx["hit_at_1"]))
        topk.append(float(sy["hit_at_k"]) - float(sx["hit_at_k"]))

    n = len(diffs_rr)
    rng = random.Random(BOOTSTRAP_SEED)
    means = sorted(
        sum(diffs_rr[rng.randrange(n)] for _ in range(n)) / n for _ in range(BOOTSTRAP_RESAMPLES)
    )
    return PairedComparison(
        n=n,
        wins=wins,
        losses=losses,
        ties=ties,
        sign_test_p=sign_test_p(wins, losses),
        top1_diff=sum(top1) / n,
        topk_diff=sum(topk) / n,
        mrr_diff=sum(diffs_rr) / n,
        mrr_diff_ci95=(
            means[int(0.025 * BOOTSTRAP_RESAMPLES)],
            means[int(0.975 * BOOTSTRAP_RESAMPLES) - 1],
        ),
    )


def format_comparison(name_a: str, name_b: str, c: PairedComparison, k: int) -> str:
    lo, hi = c.mrr_diff_ci95
    return (
        f"{name_b} vs {name_a} (n={c.n}, positive = {name_b} better)\n"
        f"  questions where {name_b} ranks the first counted file higher / lower / equal: "
        f"{c.wins} / {c.losses} / {c.ties}  (sign test p = {c.sign_test_p:.3f})\n"
        f"  Top-1 {c.top1_diff:+.1%}   Top-{k} {c.topk_diff:+.1%}   "
        f"MRR {c.mrr_diff:+.3f}  (95% bootstrap CI {lo:+.3f} to {hi:+.3f})"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.evaluation.compare", description=__doc__)
    parser.add_argument("a", type=Path, help="reference run (the 'before')")
    parser.add_argument("b", type=Path, help="run to compare against it (the 'after')")
    parser.add_argument(
        "--scope", choices=["primary", "primary_plus_supporting"], default="primary"
    )
    args = parser.parse_args(argv)

    ra = json.loads(args.a.read_text(encoding="utf-8"))
    rb = json.loads(args.b.read_text(encoding="utf-8"))
    comparison = compare_runs(ra, rb, args.scope)
    print(
        format_comparison(ra["retrieval_method"], rb["retrieval_method"], comparison, ra["top_k"])
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
