"""Invariant checks for an evidence response (plan section 10, checks 2-6).

`check_response` returns a list of violations; an empty list means every invariant holds. It
re-derives each fact independently from the files on disk, the stored parse and the graph, so
it does not trust the builder it is checking.
"""

import json
from pathlib import Path

from app.chunking.ast_chunker import _join, _split_lines
from app.evidence.builder import (
    IN_EDGES,
    MAX_RELATIONSHIPS,
    OUT_EDGES,
    RELATIONSHIP_ORDER,
    _line_and_rule,
)
from app.evidence.schema import EvidenceItem, EvidenceResponse, GraphEvidence
from app.indexing.changes import sha256_bytes
from app.indexing.live_index import LiveIndex


def _expected_edges(index: LiveIndex, node: str) -> list[tuple[str, str, int | None, str]]:
    """(relationship, other, line, rule) for every edge incident to `node`, from the graph."""
    assert index.graph is not None
    graph = index.graph.graph
    found: list[tuple[str, str, int | None, str]] = []
    if not graph.has_node(node):
        return found
    for _, target, kind, data in graph.out_edges(node, keys=True, data=True):
        if kind in OUT_EDGES:
            found.append((OUT_EDGES[kind], target, *_line_and_rule(data, kind)))
    for source, _, kind, data in graph.in_edges(node, keys=True, data=True):
        if kind in IN_EDGES:
            found.append((IN_EDGES[kind], source, *_line_and_rule(data, kind)))
    return found


def _check_graph(
    index: LiveIndex, item: EvidenceItem, graph: GraphEvidence, rank_of_node: dict[str, int]
) -> list[str]:
    bad: list[str] = []
    where = f"{item.file}:{item.line_start}"
    expected = _expected_edges(index, graph.node)
    order = {name: i for i, name in enumerate(RELATIONSHIP_ORDER)}
    expected.sort(key=lambda e: (order[e[0]], e[1], e[2] or 0, e[3]))

    shown = [(r.relationship, r.other, r.line, r.rule) for r in graph.relationships]
    if shown != expected[:MAX_RELATIONSHIPS]:
        bad.append(f"{where}: relationships differ from the graph's edges (or their order)")
    if graph.truncated != (len(expected) > MAX_RELATIONSHIPS):
        bad.append(f"{where}: truncated flag is wrong")
    for name in RELATIONSHIP_ORDER:
        if getattr(graph.degree, name) != sum(1 for e in expected if e[0] == name):
            bad.append(f"{where}: degree.{name} is wrong")
    assert index.graph is not None
    for r in graph.relationships:
        if r.other_file != index.graph.file_of(r.other):
            bad.append(f"{where}: other_file of {r.other} is wrong")

    brute = sorted(
        (rank_of_node[other], order[rel], line or 0, rule)
        for rel, other, line, rule in expected
        if other in rank_of_node and rank_of_node[other] != item.rank
    )
    links = [(k.other_rank, order[k.relationship], k.line or 0, k.rule)
             for k in graph.links_to_other_results]  # fmt: skip
    if links != brute:
        bad.append(f"{where}: links_to_other_results differ from a brute-force computation")
    return bad


def check_response(response: EvidenceResponse, index: LiveIndex, root: Path) -> list[str]:
    bad: list[str] = []
    again = EvidenceResponse.model_validate_json(response.model_dump_json())
    if again.model_dump_json() != response.model_dump_json():
        bad.append("the response does not survive a schema round trip")
    rank_of_node = {i.graph.node: i.rank for i in response.evidence if i.graph}
    previous = float("inf")
    for expected_rank, item in enumerate(response.evidence, 1):
        where = f"{item.file}:{item.line_start}"
        if item.rank != expected_rank:
            bad.append(f"{where}: rank {item.rank} at position {expected_rank}")
        if item.scores.rrf > previous:
            bad.append(f"{where}: rrf score increases down the list")
        previous = item.scores.rrf

        # lines and file version
        data = (root / item.file).read_bytes()
        if sha256_bytes(data) != item.file_sha256:
            bad.append(f"{where}: file_sha256 does not match the file on disk")
        lines = _split_lines(data.decode("utf-8", errors="replace"))
        if not 1 <= item.line_start <= item.line_end <= len(lines):
            bad.append(f"{where}: lines {item.line_start}-{item.line_end} out of range")
            continue
        if item.text is not None and item.text != _join(lines, item.line_start, item.line_end):
            bad.append(f"{where}: text differs from the file's lines")
        for name, comp in (("bm25", item.scores.bm25), ("embedding", item.scores.embedding)):
            if comp and not 1 <= comp.line_start <= comp.line_end <= len(lines):
                bad.append(f"{where}: {name} lines out of range")

        # symbol
        record = index.records.get(item.file)
        if record is None:
            bad.append(f"{where}: no record in the index")
            continue
        if item.symbol_status == "exact":
            s = item.symbol
            match = [
                p for p in record.parsed.symbols
                if s and p.start_line == s.line_start and p.qualified_name == s.qualified_name
            ]  # fmt: skip
            if not s or not match:
                bad.append(f"{where}: exact symbol not found in the stored parse")
            else:
                if match[0].end_line != s.line_end or match[0].kind != s.kind:
                    bad.append(f"{where}: symbol extent or kind differs from the parse")
                if not s.line_start <= item.line_start <= item.line_end <= s.line_end:
                    bad.append(f"{where}: item lines are outside the symbol")
                if s.node_id != f"{item.file}::{s.qualified_name}":
                    bad.append(f"{where}: node_id is malformed")
                if index.graph and not index.graph.graph.has_node(s.node_id):
                    bad.append(f"{where}: symbol node {s.node_id} is not in the graph")
        elif item.symbol is not None:
            bad.append(f"{where}: status {item.symbol_status} but a symbol is set")

        if item.graph is not None:
            expected_node = item.symbol.node_id if item.symbol else item.file
            if item.graph.node != expected_node:
                bad.append(f"{where}: graph node should be {expected_node}")
            bad.extend(_check_graph(index, item, item.graph, rank_of_node))
    return bad


def evidence_json(response: EvidenceResponse) -> str:
    """The deterministic part of a response, serialised (timing excluded by construction)."""
    return json.dumps([i.model_dump(mode="json") for i in response.evidence])


EMBEDDING_SCORE_TOLERANCE = 1.5e-6  # float32 noise of the vector store, plus 6-decimal rounding


def compare_evidence(a: EvidenceResponse, b: EvidenceResponse) -> list[str]:
    """Differences between two responses to the same request. Everything must be identical
    except the embedding raw score, which the vector store returns with float32 noise (about
    3e-8, measured in Milestone 12); it is compared within `EMBEDDING_SCORE_TOLERANCE`."""
    if len(a.evidence) != len(b.evidence):
        return [f"{len(a.evidence)} items vs {len(b.evidence)}"]
    diffs: list[str] = []
    for x, y in zip(a.evidence, b.evidence, strict=True):
        ex, ey = x.scores.embedding, y.scores.embedding
        if (ex is None) != (ey is None):
            diffs.append(f"rank {x.rank}: embedding component present in only one")
        elif ex and ey and abs(ex.score - ey.score) > EMBEDDING_SCORE_TOLERANCE:
            diffs.append(f"rank {x.rank}: embedding score {ex.score} vs {ey.score}")
        # compare as serialised (6-decimal scores), as a client would see them
        dx, dy = x.model_dump(mode="json"), y.model_dump(mode="json")
        for d in (dx, dy):
            if d["scores"]["embedding"]:
                d["scores"]["embedding"]["score"] = None
        if dx != dy:
            diffs.append(f"rank {x.rank} ({x.file}:{x.line_start}) differs")
    for field in ("query", "index", "retrieval", "notes", "schema_version"):
        if getattr(a, field) != getattr(b, field):
            diffs.append(f"{field} differs")
    return diffs
