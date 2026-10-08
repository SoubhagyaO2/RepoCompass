"""Graph-enhanced retrieval: expand the hybrid ranking with the code graph's neighbours.

Pre-registered design (evaluation/plans/m10-code-graph.md), not tuned on the benchmark:

* anchors   : the top ANCHORS unique files of the first-stage (hybrid) ranking;
* neighbours: files connected to an anchor by imports or calls, in either direction;
* votes     : every (anchor, neighbour) pair gives the neighbour 1 / (RRF_K + anchor rank),
              once per pair however many relations connect the two files;
* score     : the file's hybrid (RRF) score, or 0 if the hybrid did not return it, plus its votes.

So a file that retrieval found *and* that sits next to the top results in the graph rises, and a
neighbour retrieval missed can enter the ranking. Every expansion keeps the graph edges that
justify it ({source, relationship, target, evidence}).
"""

import time
from collections import defaultdict

from pydantic import BaseModel

from app.chunking.categories import classify_file
from app.graph.model import CodeGraph, GraphEdge
from app.models.search import SearchHit
from app.retrieval.hybrid import RRF_K, SearchFn

ANCHORS = 3
EVIDENCE_TOP = 10  # full edge evidence is attached to pairs whose neighbour ends up in this top
TRACE_FINAL = 30  # how many final entries each trace records


class PairExpansion(BaseModel):
    anchor: str
    anchor_rank: int
    neighbor: str
    neighbor_category: str
    vote: float
    relations: dict[str, int]  # relationship -> number of connecting edges
    edges: list[GraphEdge] = []  # one example edge per relationship, for pairs that matter


class FinalEntry(BaseModel):
    file_path: str
    hybrid_rank: int | None  # position in the first-stage ranking; None if it did not return it
    final_rank: int
    hybrid_score: float
    graph_vote: float
    fused_score: float


class ExpansionTrace(BaseModel):
    anchors: list[str]
    pairs: list[PairExpansion]
    final: list[FinalEntry]
    first_stage_ms: float
    expand_ms: float


class GraphExpandedRetriever:
    def __init__(
        self,
        first_stage: SearchFn,
        graph: CodeGraph,
        anchors: int = ANCHORS,
        k: int = RRF_K,
    ) -> None:
        self._first_stage = first_stage
        self._graph = graph
        self._anchors = anchors
        self._k = k
        self.last_trace: ExpansionTrace | None = None

    def search(self, query: str, depth: int) -> list[SearchHit]:
        """`depth` is passed to the first stage (chunks per component, before deduplication)."""
        t0 = time.perf_counter()
        by_file: dict[str, SearchHit] = {}
        for hit in self._first_stage(query, depth):
            by_file.setdefault(hit.file_path, hit)  # first (best) hit per file
        hybrid_order = list(by_file)
        t1 = time.perf_counter()

        anchors = hybrid_order[: self._anchors]
        votes: dict[str, float] = defaultdict(float)
        pairs: list[PairExpansion] = []
        edges_of: dict[tuple[str, str], list[GraphEdge]] = {}
        for rank, anchor in enumerate(anchors, 1):
            vote = 1 / (self._k + rank)
            for neighbor, edges in sorted(self._graph.file_neighbors(anchor).items()):
                votes[neighbor] += vote
                edges_of[(anchor, neighbor)] = edges
                relations: dict[str, int] = defaultdict(int)
                for edge in edges:
                    relations[edge.relationship] += 1
                pairs.append(
                    PairExpansion(
                        anchor=anchor,
                        anchor_rank=rank,
                        neighbor=neighbor,
                        neighbor_category=classify_file(neighbor),
                        vote=vote,
                        relations=dict(relations),
                    )
                )

        hybrid_rank = {f: i for i, f in enumerate(hybrid_order, 1)}
        fused = {f: by_file[f].score if f in by_file else 0.0 for f in {*by_file, *votes}}
        for f, v in votes.items():
            fused[f] += v
        ranking = sorted(fused, key=lambda f: (-fused[f], hybrid_rank.get(f, 10**9), f))

        results = [
            by_file[f].model_copy(update={"score": fused[f]})
            if f in by_file
            else SearchHit(  # a neighbour retrieval missed: no representative chunk
                file_path=f,
                start_line=1,
                end_line=1,
                score=fused[f],
                content="",
                category=classify_file(f),
                symbol_type="graph-expansion",
            )  # fmt: skip
            for f in ranking
        ]
        t2 = time.perf_counter()

        final_rank = {f: i for i, f in enumerate(ranking, 1)}
        for pair in pairs:
            if final_rank[pair.neighbor] <= EVIDENCE_TOP:
                seen: set[str] = set()
                for edge in edges_of[(pair.anchor, pair.neighbor)]:
                    if edge.relationship not in seen:
                        seen.add(edge.relationship)
                        pair.edges.append(edge)
        self.last_trace = ExpansionTrace(
            anchors=anchors,
            pairs=pairs,
            final=[
                FinalEntry(
                    file_path=f,
                    hybrid_rank=hybrid_rank.get(f),
                    final_rank=final_rank[f],
                    hybrid_score=by_file[f].score if f in by_file else 0.0,
                    graph_vote=votes.get(f, 0.0),
                    fused_score=fused[f],
                )
                for f in ranking[:TRACE_FINAL]
            ],
            first_stage_ms=(t1 - t0) * 1000,
            expand_ms=(t2 - t1) * 1000,
        )
        return results
