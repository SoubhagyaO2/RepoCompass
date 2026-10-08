"""M10 follow-up: what scoping function-local imports changed in the Flask graph.

    python -m app.evaluation.audit_graph_scope_fix

The old behaviour (one file-wide binding map) is exactly the new builder with every import's
scope removed, so both graphs are built from the same parses. Every changed edge is listed with
the source line it cites, for hand review, together with a fresh seeded sample of the
`import` / `import-module` call edges. Verdicts are written by hand afterwards (see README).
"""

import json
import random
import sys
from pathlib import Path
from typing import Any

from app.evaluation.experiment import DEFAULT_DATASET, RESULTS_DIR, ExperimentError, prepare
from app.graph.builder import build_graph_from_parsed
from app.graph.model import CodeGraph
from app.parsing.python_parser import parse_python_file

OUT = RESULTS_DIR / "graph_scope_fix_audit_flask.json"
SAMPLE_SEED = 1
SAMPLE_SIZE = 30


def _edges(graph: CodeGraph) -> dict[tuple[str, str, str], dict[str, Any]]:
    return {
        (u, v, k): {
            "lines": list(d.get("lines") or [d.get("line")]),
            "rules": list(d.get("rules") or [d.get("rule")]),
        }
        for u, v, k, d in graph.graph.edges(keys=True, data=True)
    }


def _source_line(root: Path, file: str, line: int | None) -> str:
    lines = (root / file).read_text(encoding="utf-8", errors="replace").split("\n")
    return lines[line - 1].strip() if line and 1 <= line <= len(lines) else "?"


def run() -> dict[str, Any]:
    prepared = prepare(DEFAULT_DATASET)
    root = prepared.ingested.local_path
    parsed = {
        f.path: parse_python_file(root, f.path)
        for f in prepared.ingested.files
        if f.language == "Python"
    }
    unscoped = {}
    for path, pf in parsed.items():
        imports = [i.model_copy(update={"scope": None}) for i in pf.imports]
        unscoped[path] = pf.model_copy(update={"imports": imports})
    new, old = build_graph_from_parsed(parsed), build_graph_from_parsed(unscoped)
    before, after = _edges(old), _edges(new)

    def listing(keys: list[tuple[str, str, str]], edges: dict[Any, Any]) -> list[dict[str, Any]]:
        out = []
        for u, v, k in keys:
            file = new.file_of(u) if u in new.graph else old.file_of(u)
            first = edges[(u, v, k)]["lines"][0]
            out.append(
                {"kind": k, "source": u, "target": v, **edges[(u, v, k)],
                 "cited_file": file, "cited_line_text": _source_line(root, file, first)}
            )  # fmt: skip
        return out

    removed = sorted(k for k in before if k not in after)
    added = sorted(k for k in after if k not in before)
    extended = sorted(k for k in before if k in after and before[k] != after[k])
    pool = sorted(
        k
        for k, e in after.items()
        if k[2] == "calls"
        and any(r in ("import", "import-module") for r in e["rules"])
        and k[0].startswith("src/flask/")
    )
    sample = random.Random(SAMPLE_SEED).sample(pool, min(SAMPLE_SIZE, len(pool)))
    return {
        "description": (
            "Effect of scoping function-local imports to their function (M10 follow-up). "
            "Verdicts are single-reviewer judgements from reading the cited line."
        ),
        "commit": prepared.dataset.commit,
        "function_local_imports": sum(1 for pf in parsed.values() for i in pf.imports if i.scope),
        "edges": {"before": len(before), "after": len(after)},
        "calls_resolved_by_rule": {
            "before": old.stats.calls_resolved_by_rule,
            "after": new.stats.calls_resolved_by_rule,
        },
        "calls_external": {"before": old.stats.calls_external, "after": new.stats.calls_external},
        "removed_edges": listing(removed, before),
        "added_edges": listing(added, after),
        "edges_with_more_cited_lines": listing(extended, after),
        "fresh_sample": {
            "population": len(pool),
            "seed": SAMPLE_SEED,
            "edges": listing(sample, after),
        },
    }


def main() -> int:
    try:
        report = run()
    except ExperimentError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    OUT.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print(
        f"edges {report['edges']}; removed {len(report['removed_edges'])}, "
        f"added {len(report['added_edges'])}, extended {len(report['edges_with_more_cited_lines'])}"
        f"\nSaved to {OUT}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
