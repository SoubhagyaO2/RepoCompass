"""Hybrid retrieval: reciprocal rank fusion (RRF) of several retrievers, at the file level.

Each component retrieves chunks; every component's chunks are deduplicated to unique files
(first occurrence = that file's rank); RRF then scores each file

    score(file) = sum over components of 1 / (RRF_K + rank of the file in that component)

and files are ordered by score. Fusing files instead of chunks means a file with many similar
chunks gets one vote per component, not one per chunk. Scores are ignored (BM25 scores and
cosine similarities are not comparable); only ranks matter, and there are no weights to tune.

RRF_K = 60 is the value from the original RRF paper (Cormack et al., 2009). It was fixed before
the benchmark was run and is not tuned on it.
"""

from collections.abc import Callable
from dataclasses import dataclass

from app.models.search import SearchHit

RRF_K = 60

SearchFn = Callable[[str, int], list[SearchHit]]


def unique_ranked_files(hits: list[SearchHit]) -> list[str]:
    """File paths in rank order, each file at the position of its best chunk."""
    seen: set[str] = set()
    files: list[str] = []
    for hit in hits:
        if hit.file_path not in seen:
            seen.add(hit.file_path)
            files.append(hit.file_path)
    return files


def reciprocal_rank_fusion(rankings: list[list[str]], k: int = RRF_K) -> list[tuple[str, float]]:
    """Fuse ranked lists of unique files. Returns (file, score), best first.

    A file missing from a list simply gets no vote from it. Ties are broken by the file's best
    rank in any list, then by path, so the order is deterministic.
    """
    scores: dict[str, float] = {}
    best_rank: dict[str, int] = {}
    for ranking in rankings:
        for rank, path in enumerate(ranking, 1):
            scores[path] = scores.get(path, 0.0) + 1 / (k + rank)
            best_rank[path] = min(best_rank.get(path, rank), rank)
    return sorted(scores.items(), key=lambda item: (-item[1], best_rank[item[0]], item[0]))


@dataclass(frozen=True)
class ComponentMatch:
    """How one component saw a file: its rank among that component's unique files and the
    component's best chunk for the file (whose `score` is the component's own raw score)."""

    rank: int
    hit: SearchHit


@dataclass(frozen=True)
class FusedFile:
    """One file of the fused ranking, with everything that produced its position."""

    path: str
    score: float  # the RRF score
    hit: SearchHit  # the displayed chunk (score = that component's raw score)
    matches: tuple[ComponentMatch | None, ...]  # per component, None = not retrieved by it


class HybridRetriever:
    """RRF over component retrievers. Returns one SearchHit per unique file, best first."""

    def __init__(self, components: list[SearchFn], k: int = RRF_K) -> None:
        self._components = components
        self._k = k

    def search(self, query: str, depth: int) -> list[SearchHit]:
        """`depth` is the number of chunks each component retrieves before deduplication."""
        return [
            f.hit.model_copy(update={"score": f.score}) for f in self.search_detailed(query, depth)
        ]

    def search_detailed(self, query: str, depth: int) -> list[FusedFile]:
        """The same ranking as `search`, with each component's rank and best chunk per file."""
        results = [component(query, depth) for component in self._components]
        rankings = [unique_ranked_files(hits) for hits in results]

        # Each component's best chunk (first occurrence) and rank for every file it retrieved.
        matches: list[dict[str, ComponentMatch]] = []
        for hits, ranking in zip(results, rankings, strict=True):
            by_file: dict[str, SearchHit] = {}
            for hit in hits:
                by_file.setdefault(hit.file_path, hit)
            matches.append({p: ComponentMatch(r, by_file[p]) for r, p in enumerate(ranking, 1)})

        # For display, represent each file by its chunk from the component that ranks it best
        # (the earlier component wins a tie).
        representative: dict[str, ComponentMatch] = {}
        for component_matches in matches:
            for path, match in component_matches.items():
                if path not in representative or match.rank < representative[path].rank:
                    representative[path] = match

        return [
            FusedFile(
                path=path,
                score=score,
                hit=representative[path].hit,
                matches=tuple(m.get(path) for m in matches),
            )
            for path, score in reciprocal_rank_fusion(rankings, self._k)
        ]
