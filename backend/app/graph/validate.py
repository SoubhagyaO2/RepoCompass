"""Validate the code graph (Milestone 10A). The benchmark questions are not used here.

    python -m app.graph.validate [--facts PATH] [--out PATH]

V1  The graph's import edges inside src/flask must equal the same relation computed with
    Python's own `ast` module (a different parser from the Tree-sitter one the graph uses).
V2  Every fact in evaluation/graph_facts/flask.json (hand-verified from the source before the
    builder existed) must hold: 'present' edges exist; 'absent' and 'known_gap' edges do not.
V3  Statistics: nodes and edges per type and how every call was resolved.
"""

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.evaluation.experiment import REPO_ROOT
from app.evaluation.validate import get_head_commit
from app.graph.builder import build_graph
from app.graph.model import CodeGraph
from app.ingestion.clone import CloneError
from app.ingestion.service import ingest_repository
from app.models.repository import IngestedRepository

DEFAULT_FACTS = REPO_ROOT / "evaluation" / "graph_facts" / "flask.json"
DEFAULT_OUT = REPO_ROOT / "evaluation" / "results" / "graph_validation_flask.json"
SCOPE = "src/flask/"


# --- V1: an independent computation of the import edges, with the `ast` module --------------


def ast_import_edges(root: Path, python_files: list[str]) -> set[tuple[str, str]]:
    """File -> file import edges for files under SCOPE, extracted with Python's `ast`."""

    def module_of(path: str) -> str:
        parts = path.removesuffix(".py").split("/")
        parts = parts[1:] if parts[0] == "src" and len(parts) > 1 else parts
        return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)

    by_module = {module_of(p): p for p in reversed(python_files)}  # first file wins
    edges: set[tuple[str, str]] = set()
    for path in python_files:
        if not path.startswith(SCOPE):
            continue
        package = module_of(path).split(".")
        if not path.endswith("__init__.py"):
            package = package[:-1]
        tree = ast.parse((root / path).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            targets: list[str | None] = []
            if isinstance(node, ast.Import):
                targets = [by_module.get(alias.name) for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0:
                    base = node.module or ""
                elif node.level - 1 > len(package):
                    continue
                else:
                    kept = package[: len(package) - (node.level - 1)]
                    base = ".".join([*kept, *([node.module] if node.module else [])])
                if not base:
                    continue
                for alias in node.names:
                    submodule = by_module.get(f"{base}.{alias.name}") if alias.name != "*" else None
                    targets.append(submodule or by_module.get(base))
            for target in targets:
                if target and target != path:
                    edges.add((path, target))
    return edges


def graph_import_edges(graph: CodeGraph) -> set[tuple[str, str]]:
    return {
        (u, v)
        for u, v, key in graph.graph.edges(keys=True)
        if key == "imports" and u.startswith(SCOPE)
    }


# --- V2: hand-verified facts -----------------------------------------------------------------


def check_facts(graph: CodeGraph, facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One result per fact: {id, kind, expect, exists, ok, problem}."""
    results = []
    for fact in facts:
        source, target = fact["source"], fact["target"]
        missing = [n for n in (source, target) if n not in graph.graph]
        exists = not missing and graph.has_edge(fact["kind"], source, target)
        expect = fact["expect"]
        ok = exists if expect == "present" else not exists
        problem = None
        if not ok:
            if expect == "present":
                problem = (
                    f"missing node(s) {missing}" if missing else "expected edge not in the graph"
                )
            elif expect == "absent":
                problem = "edge exists but must not (a false positive)"
            else:
                problem = "a documented known gap is now present; update the limitation"
        results.append(
            {
                "id": fact["id"],
                "kind": fact["kind"],
                "expect": expect,
                "exists": exists,
                "ok": ok,
                "problem": problem,
            }  # fmt: skip
        )
    return results


# --- run -------------------------------------------------------------------------------------


def validate(ingested: IngestedRepository, facts_doc: dict[str, Any]) -> dict[str, Any]:
    graph = build_graph(ingested)
    python_files = [f.path for f in ingested.files if f.language == "Python"]

    from_graph = graph_import_edges(graph)
    from_ast = ast_import_edges(ingested.local_path, python_files)
    fact_results = check_facts(graph, facts_doc["facts"])
    failures = [r for r in fact_results if not r["ok"]]

    by_group: dict[str, dict[str, int]] = {}
    for r in fact_results:
        group = by_group.setdefault(f"{r['kind']}/{r['expect']}", {"ok": 0, "failed": 0})
        group["ok" if r["ok"] else "failed"] += 1

    return {
        "repository": facts_doc["repository"],
        "commit": facts_doc["commit"],
        "facts_file": facts_doc["name"],
        "v1_import_edges_vs_ast": {
            "scope": SCOPE,
            "edges_in_graph": len(from_graph),
            "edges_from_ast": len(from_ast),
            "identical": from_graph == from_ast,
            "only_in_graph": sorted(map(list, from_graph - from_ast)),
            "only_in_ast": sorted(map(list, from_ast - from_graph)),
        },
        "v2_facts": {
            "total": len(fact_results),
            "passed": len(fact_results) - len(failures),
            "failed": len(failures),
            "by_kind_and_expectation": by_group,
            "failures": failures,
        },
        "v3_stats": graph.stats.model_dump(),
    }


def format_summary(result: dict[str, Any]) -> str:
    v1, v2, v3 = result["v1_import_edges_vs_ast"], result["v2_facts"], result["v3_stats"]
    calls = v3["calls_total"]
    resolved = sum(v3["calls_resolved_by_rule"].values())
    lines = [
        f"Code graph of {result['repository']} @ {result['commit'][:10]}  "
        f"({v3['files']} files, built in {v3['build_seconds']:.2f}s)",
        f"  nodes: {v3['nodes_by_type']}",
        f"  edges: {v3['edges_by_type']}",
        f"  calls: {calls:,} call expressions -> {resolved:,} resolved "
        f"({v3['calls_resolved_by_rule']})",
        f"         skipped: {v3['calls_external']:,} external receivers, "
        f"{v3['calls_builtin_name']:,} builtin-type method names, "
        f"{v3['calls_ambiguous']:,} ambiguous, {v3['calls_unresolved']:,} unresolved",
        "",
        f"V1 import edges in {v1['scope']} vs Python's ast module: "
        f"{'IDENTICAL' if v1['identical'] else 'DIFFERENT'} "
        f"({v1['edges_in_graph']} vs {v1['edges_from_ast']} edges)",
        f"V2 hand-verified facts: {v2['passed']}/{v2['total']} hold",
    ]
    for group, c in sorted(v2["by_kind_and_expectation"].items()):
        lines.append(f"     {group:<22} {c['ok']} ok, {c['failed']} failed")
    for edge in v1["only_in_graph"][:5]:
        lines.append(f"  only in graph: {edge}")
    for edge in v1["only_in_ast"][:5]:
        lines.append(f"  only in ast:   {edge}")
    for f in v2["failures"]:
        lines.append(f"  FAILED {f['id']} ({f['kind']}/{f['expect']}): {f['problem']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.graph.validate", description=__doc__)
    parser.add_argument("--facts", type=Path, default=DEFAULT_FACTS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    facts_doc = json.loads(args.facts.read_text(encoding="utf-8"))
    try:
        ingested = ingest_repository(
            f"https://github.com/{facts_doc['repository']}", get_settings()
        )
    except CloneError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    head = get_head_commit(ingested.local_path)
    if head != facts_doc["commit"]:
        print(
            f"error: checkout is at {head}, facts are pinned to {facts_doc['commit']}",
            file=sys.stderr,
        )
        return 1

    result = validate(ingested, facts_doc)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(format_summary(result))
    print(f"\nSaved to {args.out}")
    ok = result["v1_import_edges_vs_ast"]["identical"] and result["v2_facts"]["failed"] == 0
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
