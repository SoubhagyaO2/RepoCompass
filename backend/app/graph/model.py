"""The graph object and the evidence records it hands out.

Node ids: a file is its repo-relative path ("src/flask/app.py"); a symbol is
"<path>::<qualified name>" ("src/flask/app.py::Flask.wsgi_app"). Overloads of one name in one
file share a node. Edge kinds (MultiDiGraph keys): "defines", "imports", "inherits", "calls".
"""

import json
from collections import defaultdict
from typing import Any

import networkx as nx
from pydantic import BaseModel


class Evidence(BaseModel):
    """Where a relationship comes from: the file and line of the import/call/class statement."""

    file: str
    line: int | None
    rule: str  # how it was resolved, e.g. "self", "import", "unique-name"


class GraphEdge(BaseModel):
    """One relationship, as seen from the first node. Safe to log or show to a user."""

    source: str
    relationship: str  # imports | imported_by | calls | called_by | inherits | defines
    target: str
    evidence: Evidence


class BuildStats(BaseModel):
    files: int
    nodes_by_type: dict[str, int]
    edges_by_type: dict[str, int]
    calls_total: int
    calls_resolved_by_rule: dict[str, int]
    calls_ambiguous: int  # skipped: the method name is defined by several classes
    calls_unresolved: int  # skipped: builtins, dynamic access, names with no definition
    calls_external: int = 0  # skipped: the receiver is bound by an import from outside the repo
    calls_builtin_name: int = 0  # skipped: the method name also exists on builtin types
    build_seconds: float


class CodeGraph:
    def __init__(self, graph: "nx.MultiDiGraph[str]", stats: BuildStats) -> None:
        self.graph = graph
        self.stats = stats
        self._file_neighbors: dict[str, dict[str, list[GraphEdge]]] = defaultdict(
            lambda: defaultdict(list)
        )
        self._index_file_edges()

    # --- queries -----------------------------------------------------------------------

    def has_edge(self, kind: str, source: str, target: str) -> bool:
        return bool(self.graph.has_edge(source, target, key=kind))

    def node_type(self, node: str) -> str | None:
        data = self.graph.nodes.get(node)
        return data["type"] if data else None

    def file_of(self, node: str) -> str:
        return str(self.graph.nodes[node]["path"])

    def definitions(self, name: str) -> list[str]:
        """Symbols whose simple name or qualified name is `name`."""
        return sorted(
            n
            for n, d in self.graph.nodes(data=True)
            if d["type"] != "file" and name in (d["name"], d["qualified_name"])
        )

    def callers_of(self, symbol: str) -> list[GraphEdge]:
        return self._call_edges(self.graph.in_edges(symbol, keys=True, data=True), "calls")

    def callees_of(self, symbol: str) -> list[GraphEdge]:
        return self._call_edges(self.graph.out_edges(symbol, keys=True, data=True), "calls")

    def importers_of(self, path: str) -> list[GraphEdge]:
        return self._call_edges(self.graph.in_edges(path, keys=True, data=True), "imports")

    def imports_of(self, path: str) -> list[GraphEdge]:
        return self._call_edges(self.graph.out_edges(path, keys=True, data=True), "imports")

    def file_neighbors(self, path: str) -> dict[str, list[GraphEdge]]:
        """Files connected to `path` by imports or calls in either direction, each with the
        edges that connect them (seen from `path`'s side)."""
        return {n: list(edges) for n, edges in self._file_neighbors.get(path, {}).items()}

    # --- export ------------------------------------------------------------------------

    def to_json(self) -> str:
        """Node-link JSON for inspection (edge attributes included)."""
        data: dict[str, Any] = nx.node_link_data(self.graph, edges="edges")
        return json.dumps({"stats": self.stats.model_dump(), "graph": data}, indent=1, default=list)

    # --- internals ---------------------------------------------------------------------

    def _call_edges(self, edges: Any, kind: str) -> list[GraphEdge]:
        found: list[GraphEdge] = []
        for source, target, key, data in edges:
            if key != kind:
                continue
            lines = data.get("lines") or [data.get("line")]
            rules = data.get("rules") or [data.get("rule", kind)]
            found.append(
                GraphEdge(
                    source=source,
                    relationship=kind,
                    target=target,
                    evidence=Evidence(
                        file=self.file_of(source), line=lines[0], rule=",".join(rules)
                    ),
                )
            )
        return sorted(found, key=lambda e: (e.source, e.target))

    def _index_file_edges(self) -> None:
        """Pre-compute file-level neighbours from import and call edges."""
        for u, v, key, data in self.graph.edges(keys=True, data=True):
            if key not in ("imports", "calls"):
                continue
            file_u, file_v = self.file_of(u), self.file_of(v)
            if file_u == file_v:
                continue
            line = (data.get("lines") or [None])[0]
            rule = ",".join(data.get("rules") or [key])
            if key == "imports":
                out = GraphEdge(
                    source=u, relationship="imports", target=v,
                    evidence=Evidence(file=file_u, line=line, rule=rule),
                )  # fmt: skip
                back = GraphEdge(
                    source=v, relationship="imported_by", target=u,
                    evidence=Evidence(file=file_u, line=line, rule=rule),
                )  # fmt: skip
            else:
                out = GraphEdge(
                    source=u, relationship="calls", target=v,
                    evidence=Evidence(file=file_u, line=line, rule=rule),
                )  # fmt: skip
                back = GraphEdge(
                    source=v, relationship="called_by", target=u,
                    evidence=Evidence(file=file_u, line=line, rule=rule),
                )  # fmt: skip
            self._file_neighbors[file_u][file_v].append(out)
            self._file_neighbors[file_v][file_u].append(back)
