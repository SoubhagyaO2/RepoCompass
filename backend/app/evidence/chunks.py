"""The additional-chunk selection policy of Milestone 14 (plan section 2), as pure functions.

Candidates are chunks of a retrieved file that the retrieval components already returned; each is
scored with a chunk-level reciprocal rank fusion using the existing constant, and the best `n`
(excluding the primary chunk) are chosen. Nothing is tuned.
"""

from collections.abc import Sequence

from app.retrieval.hybrid import RRF_K, ChunkCandidate


def chunk_rrf(candidate: ChunkCandidate, k: int = RRF_K) -> float:
    """sum over components of 1 / (k + position in that component's chunk list)."""
    return sum(1 / (k + p) for p in candidate.positions if p is not None)


def select_additional(
    candidates: Sequence[ChunkCandidate], primary: tuple[int, int], n: int, k: int = RRF_K
) -> list[ChunkCandidate]:
    """The `n` best chunks other than the primary one: highest `chunk_rrf`, ties by the better
    single-component position, then by `start_line`. Chunks no component returned score 0 and
    are never chosen. Returns fewer than `n` when fewer candidates exist."""
    eligible = [
        c
        for c in candidates
        if (c.start_line, c.end_line) != primary and any(p is not None for p in c.positions)
    ]
    eligible.sort(
        key=lambda c: (
            -chunk_rrf(c, k),
            min(p for p in c.positions if p is not None),
            c.start_line,
        )
    )
    return eligible[: max(n, 0)]
