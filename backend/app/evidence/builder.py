"""Turns the hybrid ranking into evidence items. A pure view over retrieval: it never adds,
drops or reorders files, and graph facts annotate items without influencing them."""

import json
from typing import Any

from app.evidence.schema import (
    ComponentScore,
    EvidenceItem,
    EvidenceResponse,
    GraphDegree,
    GraphEvidence,
    GraphLink,
    GraphRelationship,
    IndexInfo,
    Relationship,
    RetrievalInfo,
    Scores,
    SymbolInfo,
)
from app.indexing.changes import sha256_text
from app.indexing.live_index import FileRecord, IndexedChunk, LiveIndex
from app.retrieval.hybrid import RRF_K, ComponentMatch, FusedFile, HybridRetriever

MAX_RELATIONSHIPS = 10
MAX_QUERY_CHARS = 2000
DEPTH = 50  # chunks per component before deduplication: the depth used in every benchmark
DEFAULT_TOP_K = 10
COMPONENTS = ["bm25", "embedding"]
RELATIONSHIP_ORDER: list[Relationship] = [
    "calls", "called_by", "inherits", "imports", "imported_by", "defines",
]  # fmt: skip
VIEW = "ast"

# (graph edge kind, direction seen from the item's node) -> relationship name
OUT_EDGES: dict[str, Relationship] = {
    "calls": "calls", "inherits": "inherits", "imports": "imports", "defines": "defines",
}  # fmt: skip
IN_EDGES: dict[str, Relationship] = {"calls": "called_by", "imports": "imported_by"}


class EvidenceError(RuntimeError):
    """The index is inconsistent with what retrieval returned (a bug, not a user error)."""


class EvidenceQueryError(ValueError):
    """The request is invalid (empty or too long query, bad top_k)."""


def _line_and_rule(data: dict[str, Any], kind: str) -> tuple[int | None, str]:
    lines = data.get("lines") or [data.get("line")]
    rules = data.get("rules") or [data.get("rule", kind)]
    return lines[0], ",".join(rules)


class EvidenceBuilder:
    def __init__(self, index: LiveIndex, revision: str | None = None) -> None:
        self.index = index
        self.revision = revision

    # --- public ----------------------------------------------------------------------------

    def build(
        self, query: str, top_k: int = DEFAULT_TOP_K, include_text: bool = False
    ) -> EvidenceResponse:
        if not query.strip():
            raise EvidenceQueryError("the query is empty")
        if len(query) > MAX_QUERY_CHARS:
            raise EvidenceQueryError(f"the query is longer than {MAX_QUERY_CHARS} characters")
        if not 1 <= top_k <= DEPTH:
            raise EvidenceQueryError(f"top_k must be between 1 and {DEPTH}")

        retriever = HybridRetriever(
            [self.index.bm25_search(VIEW), self.index.embedding_search(VIEW)], RRF_K
        )
        fused = retriever.search_detailed(query, DEPTH)[:top_k]
        nodes = {f.path: self._node_of(f) for f in fused}
        rank_of_node = {node: rank for rank, node in enumerate(nodes.values(), 1)}
        items = [
            self._item(rank, f, nodes[f.path], rank_of_node, include_text)
            for rank, f in enumerate(fused, 1)
        ]
        return EvidenceResponse(
            query=query,
            index=self.index_info(),
            retrieval=RetrievalInfo(
                method="hybrid-rrf", rrf_k=RRF_K, depth=DEPTH, components=COMPONENTS
            ),
            evidence=items,
        )

    def index_info(self) -> IndexInfo:
        index = self.index
        config = index.config.model_dump()
        files = {p: r.sha256 for p, r in sorted(index.records.items())}
        return IndexInfo(
            repository=index.repository,
            revision=self.revision,
            view=VIEW,
            files=len(index.records),
            chunks=len(index.chunks[VIEW]),
            config_sha256=sha256_text(json.dumps(config, sort_keys=True)),
            manifest_sha256=sha256_text(json.dumps({"config": config, "files": files})),
        )

    # --- one item --------------------------------------------------------------------------

    def _record(self, path: str) -> FileRecord:
        record = self.index.records.get(path)
        if record is None:
            raise EvidenceError(f"retrieval returned {path}, which the index has no record of")
        return record

    @staticmethod
    def _chunk(record: FileRecord, start: int, end: int) -> IndexedChunk:
        for chunk in record.chunks[VIEW]:
            if chunk.start_line == start:
                if chunk.end_line != end:
                    raise EvidenceError(f"{record.path}:{start}: chunk lines disagree")
                return chunk
        raise EvidenceError(f"{record.path}:{start}: no AST chunk starts at this line")

    @staticmethod
    def _symbol(record: FileRecord, chunk: IndexedChunk) -> SymbolInfo | None:
        """The parse symbol the chunk is, identified by (file, start line); never guessed."""
        if chunk.symbol_type == "module" or chunk.symbol_name is None:
            return None
        for sym in record.parsed.symbols:
            if sym.start_line == chunk.start_line and sym.qualified_name == chunk.symbol_name:
                return SymbolInfo(
                    qualified_name=sym.qualified_name,
                    name=sym.name,
                    kind=sym.kind,
                    node_id=f"{record.path}::{sym.qualified_name}",
                    line_start=sym.start_line,
                    line_end=sym.end_line,
                )
        return None

    def _node_of(self, fused: FusedFile) -> str:
        record = self._record(fused.path)
        chunk = self._chunk(record, fused.hit.start_line, fused.hit.end_line)
        symbol = self._symbol(record, chunk)
        return symbol.node_id if symbol else fused.path

    def _item(
        self,
        rank: int,
        fused: FusedFile,
        node: str,
        rank_of_node: dict[str, int],
        include_text: bool,
    ) -> EvidenceItem:
        record = self._record(fused.path)
        chunk = self._chunk(record, fused.hit.start_line, fused.hit.end_line)
        symbol = self._symbol(record, chunk)
        if chunk.symbol_type == "module":
            status = "module_level"
        else:
            status = "exact" if symbol else "unparsed"
        bm25, embedding = (self._component(m) for m in fused.matches)
        return EvidenceItem(
            rank=rank,
            file=fused.path,
            file_sha256=record.sha256,
            category=chunk.category,
            line_start=chunk.start_line,
            line_end=chunk.end_line,
            symbol=symbol,
            symbol_status=status,
            scores=Scores(rrf=fused.score, bm25=bm25, embedding=embedding),
            graph=self._graph(node, rank, rank_of_node),
            text=chunk.content if include_text else None,
        )

    @staticmethod
    def _component(match: ComponentMatch | None) -> ComponentScore | None:
        if match is None:
            return None
        return ComponentScore(
            rank=match.rank,
            score=match.hit.score,
            line_start=match.hit.start_line,
            line_end=match.hit.end_line,
        )

    # --- graph annotations -----------------------------------------------------------------

    def _graph(self, node: str, rank: int, rank_of_node: dict[str, int]) -> GraphEvidence | None:
        code_graph = self.index.graph
        if code_graph is None:
            return None
        graph = code_graph.graph
        found: list[GraphRelationship] = []
        if graph.has_node(node):
            for _, target, kind, data in graph.out_edges(node, keys=True, data=True):
                if kind in OUT_EDGES:
                    found.append(self._relationship(OUT_EDGES[kind], target, kind, data))
            for source, _, kind, data in graph.in_edges(node, keys=True, data=True):
                if kind in IN_EDGES:
                    found.append(self._relationship(IN_EDGES[kind], source, kind, data))
        order = {name: i for i, name in enumerate(RELATIONSHIP_ORDER)}
        found.sort(key=lambda r: (order[r.relationship], r.other, r.line or 0, r.rule))

        degree = GraphDegree()
        for r in found:
            setattr(degree, r.relationship, getattr(degree, r.relationship) + 1)
        links = [
            GraphLink(relationship=r.relationship, other_rank=rank_of_node[r.other],
                      line=r.line, rule=r.rule)
            for r in found
            if r.other in rank_of_node and rank_of_node[r.other] != rank
        ]  # fmt: skip
        links.sort(key=lambda k: (k.other_rank, order[k.relationship], k.line or 0, k.rule))
        return GraphEvidence(
            node=node,
            degree=degree,
            relationships=found[:MAX_RELATIONSHIPS],
            truncated=len(found) > MAX_RELATIONSHIPS,
            links_to_other_results=links,
        )

    def _relationship(
        self, relationship: Relationship, other: str, kind: str, data: dict[str, Any]
    ) -> GraphRelationship:
        assert self.index.graph is not None
        line, rule = _line_and_rule(data, kind)
        return GraphRelationship(
            relationship=relationship,
            other=other,
            other_file=self.index.graph.file_of(other),
            line=line,
            rule=rule,
        )
