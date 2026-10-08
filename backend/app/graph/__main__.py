"""Inspect the code graph of a repository.

python -m app.graph <github-url> --callers "src/flask/app.py::Flask.ensure_sync"
python -m app.graph <github-url> --importers src/flask/sessions.py
python -m app.graph <github-url> --find ensure_sync
python -m app.graph <github-url> --neighbors src/flask/sessions.py
"""

import argparse
import sys

from app.config import get_settings
from app.graph.builder import build_graph
from app.graph.model import GraphEdge
from app.ingestion.clone import CloneError
from app.ingestion.service import ingest_repository
from app.ingestion.url import InvalidRepoURLError


def _print_edges(title: str, edges: list[GraphEdge]) -> None:
    print(f"\n{title} ({len(edges)})")
    for e in edges:
        where = f"{e.evidence.file}:{e.evidence.line}"
        print(f"  {e.source}  --{e.relationship}-->  {e.target}   [{where}, {e.evidence.rule}]")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.graph", description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--find", metavar="NAME", help="symbols with this (qualified) name")
    parser.add_argument("--callers", metavar="SYMBOL")
    parser.add_argument("--callees", metavar="SYMBOL")
    parser.add_argument("--importers", metavar="FILE")
    parser.add_argument("--imports", metavar="FILE")
    parser.add_argument("--neighbors", metavar="FILE", help="files connected by imports or calls")
    args = parser.parse_args(argv)

    try:
        graph = build_graph(ingest_repository(args.url, get_settings()))
    except (InvalidRepoURLError, CloneError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    s = graph.stats
    print(f"{s.files} files; nodes {s.nodes_by_type}; edges {s.edges_by_type}")
    print(f"built in {s.build_seconds:.2f}s")
    if args.find:
        print("\n" + "\n".join(graph.definitions(args.find) or ["(none)"]))
    if args.callers:
        _print_edges(f"callers of {args.callers}", graph.callers_of(args.callers))
    if args.callees:
        _print_edges(f"callees of {args.callees}", graph.callees_of(args.callees))
    if args.importers:
        _print_edges(f"files importing {args.importers}", graph.importers_of(args.importers))
    if args.imports:
        _print_edges(f"files imported by {args.imports}", graph.imports_of(args.imports))
    if args.neighbors:
        for neighbor, edges in sorted(graph.file_neighbors(args.neighbors).items()):
            kinds = ", ".join(sorted({e.relationship for e in edges}))
            print(f"  {neighbor}   [{kinds}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
