"""Milestone 11: incremental indexing, measured and checked against a full rebuild.

    python -m app.evaluation.run_incremental [--reps 3]

Plan, scenarios and correctness criteria: evaluation/plans/m11-incremental-indexing.md (committed
before this existed). The working tree is a scratch copy of the pinned Flask checkout; the
original clone is never modified.
"""

import argparse
import json
import random
import shutil
import statistics
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.evaluation.experiment import (
    DEFAULT_DATASET,
    RESULTS_DIR,
    RETRIEVAL_DEPTH,
    ExperimentError,
    environment,
    prepare,
)
from app.evaluation.run_hybrid import FILES as HYBRID_RESULT_FILES
from app.indexing.live_index import VIEWS, LiveIndex, UpdateStats
from app.parsing.python_parser import parse_python_source
from app.retrieval.embedder import Embedder, HashingEmbedder, get_embedder
from app.retrieval.hybrid import unique_ranked_files
from app.retrieval.vector_store import VectorStore

OUT = RESULTS_DIR / "incremental_indexing_flask.json"
APP = "src/flask/app.py"
VECTOR_SAMPLE = 30  # reused vectors re-embedded per chunking per scenario (plus all changed chunks)
COSINE_FLOOR = 0.999999

NEW_MODULE = (
    '"""A module added by the benchmark."""\n\n\n'
    "class Extra:\n    def describe(self):\n        return 'extra'\n\n\n"
    "def make_extra():\n    return Extra()\n\n\n"
    "def use_extra():\n    return make_extra().describe()\n"
)
SHADOW_MODULE = "class Shadow:\n    def make_shell_context(self):\n        return {}\n"


# --- scenarios -------------------------------------------------------------------------------


@dataclass
class Scenario:
    id: str
    description: str
    paths: list[str]  # every path the change touches (created, edited or deleted)
    mutate: Callable[[Path], None]
    # (source symbol, target symbol): an edge that must exist before and be gone after
    edge_disappears: tuple[str, str] | None = None


def _read(root: Path, rel: str) -> bytes:
    return (root / rel).read_bytes()


def _write(root: Path, rel: str, data: bytes) -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_bytes(data)


def _append_comment(rel: str) -> Callable[[Path], None]:
    def mutate(root: Path) -> None:
        data = _read(root, rel)
        _write(root, rel, data + (b"" if data.endswith(b"\n") else b"\n") + b"# edited\n")

    return mutate


def _edit_start(root: Path) -> None:
    _write(root, APP, b"# inserted line\n" + _read(root, APP))


def _edit_function(root: Path) -> None:
    """Insert a comment inside Flask.dispatch_request, before its last line."""
    data = _read(root, APP)
    parsed = parse_python_source(data, APP)
    symbol = next(s for s in parsed.symbols if s.qualified_name == "Flask.dispatch_request")
    lines = data.split(b"\n")
    lines.insert(symbol.end_line - 1, b"        # edited inside the function")
    _write(root, APP, b"\n".join(lines))


def _rename_symbol(root: Path) -> None:
    data = _read(root, APP)
    assert data.count(b"def ensure_sync(") == 1
    _write(root, APP, data.replace(b"def ensure_sync(", b"def ensure_sync_renamed("))


def _rename_file(root: Path) -> None:
    data = _read(root, "src/flask/logging.py")
    (root / "src/flask/logging.py").unlink()
    _write(root, "src/flask/logging_renamed.py", data)


SCENARIOS = [
    Scenario("noop", "nothing changed", [], lambda root: None),
    Scenario(
        "touch",
        "app.py rewritten with identical bytes (new mtime)",
        [APP],
        lambda root: _write(root, APP, _read(root, APP)),
    ),  # fmt: skip
    Scenario("edit_end", "one comment line appended to app.py", [APP], _append_comment(APP)),
    Scenario("edit_start", "one comment line inserted at the top of app.py", [APP], _edit_start),
    Scenario(
        "edit_function", "one comment line inside Flask.dispatch_request", [APP], _edit_function
    ),
    Scenario(
        "edit_5_files",
        "one comment appended to each of 5 library files",
        [f"src/flask/{n}.py" for n in ("ctx", "helpers", "sessions", "cli", "config")],
        lambda root: [
            _append_comment(f"src/flask/{n}.py")(root)
            for n in ("ctx", "helpers", "sessions", "cli", "config")
        ],
    ),  # fmt: skip
    Scenario(
        "add_file",
        "a new module with a class and two functions",
        ["src/flask/_extra.py"],
        lambda root: _write(root, "src/flask/_extra.py", NEW_MODULE.encode()),
    ),  # fmt: skip
    Scenario(
        "delete_file",
        "signals.py deleted (five modules import it)",
        ["src/flask/signals.py"],
        lambda root: (root / "src/flask/signals.py").unlink(),
    ),  # fmt: skip
    Scenario(
        "rename_file",
        "logging.py moved to logging_renamed.py, bytes unchanged",
        ["src/flask/logging.py", "src/flask/logging_renamed.py"],
        _rename_file,
    ),
    Scenario(
        "graph_rename_symbol",
        "Flask.ensure_sync renamed: unchanged callers lose their edge",
        [APP],
        _rename_symbol,
        edge_disappears=("src/flask/views.py::View.as_view", "src/flask/app.py::Flask.ensure_sync"),
    ),  # fmt: skip
    Scenario(
        "graph_ambiguity",
        "a second class defines make_shell_context: cli.py's edge becomes ambiguous",
        ["src/flask/_shadow.py"],
        lambda root: _write(root, "src/flask/_shadow.py", SHADOW_MODULE.encode()),
        edge_disappears=(
            "src/flask/cli.py::shell_command",
            "src/flask/app.py::Flask.make_shell_context",
        ),
    ),  # fmt: skip
]


# --- helpers ---------------------------------------------------------------------------------


def _fresh_dir(path: Path) -> Path:
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path


def _capture(root: Path, paths: list[str]) -> dict[str, bytes | None]:
    return {p: (_read(root, p) if (root / p).exists() else None) for p in paths}


def _restore(root: Path, captured: dict[str, bytes | None]) -> None:
    for rel, data in captured.items():
        if data is None:
            (root / rel).unlink(missing_ok=True)
        else:
            _write(root, rel, data)


def _view_summary(stats: UpdateStats) -> dict[str, Any]:
    return {v: stats.views[v].model_dump() for v in VIEWS}


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = (sum(x * x for x in a) * sum(y * y for y in b)) ** 0.5
    return dot / norm if norm else 0.0


def check_m8_rankings(index: LiveIndex, questions: list[Any], out_dir: Path) -> dict[str, Any]:
    """The live index must reproduce the saved Milestone 8 hybrid rankings exactly."""
    result: dict[str, Any] = {}
    for view, key in (("fixed", ("fixed", "hybrid")), ("ast", ("ast", "hybrid"))):
        saved = json.loads((out_dir / HYBRID_RESULT_FILES[key]).read_text(encoding="utf-8"))
        saved_files = {q["id"]: q["retrieved_files"] for q in saved["per_question"]}
        search = index.hybrid(view)
        mismatches = [
            q.id
            for q in questions
            if unique_ranked_files(search(q.question, RETRIEVAL_DEPTH))[:10] != saved_files[q.id]
        ]
        result[view] = {"questions": len(questions), "mismatches": mismatches}
    result["identical"] = all(not result[v]["mismatches"] for v in VIEWS)
    return result


def verify(
    index: LiveIndex,
    probes: list[str],
    ref_store: VectorStore,
    ref_state: Path,
    embedder: Embedder,
    changed_paths: list[str],
    seed: int,
) -> dict[str, Any]:
    """Criteria 1-5: equality with a from-scratch build, and real-embedding checks of vectors."""
    reference = LiveIndex(
        index.repository, index.root, _fresh_dir(ref_state), ref_store, HashingEmbedder(),
        index.chunk_size, index.chunk_overlap,
    )  # fmt: skip
    reference.build_full()
    mine, theirs = index.snapshot(probes), reference.snapshot(probes)
    parts = {key: mine[key] == theirs[key] for key in mine}

    rng = random.Random(seed)
    cos_min, checked = 1.0, 0
    for view in VIEWS:
        stored = index.vectors(view)
        in_changed = [
            c.id for p in changed_paths if p in index.records for c in index.records[p].chunks[view]
        ]
        others = [cid for cid in stored if cid not in set(in_changed)]
        ids = in_changed + rng.sample(others, min(VECTOR_SAMPLE, len(others)))
        fresh = embedder.embed_documents([index.chunks[view][i].content for i in ids])
        for cid, vector in zip(ids, fresh, strict=True):
            cos_min = min(cos_min, _cosine(stored[cid], vector))
            checked += 1
    return {
        "identical_to_full_rebuild": all(parts.values()),
        "parts": parts,
        "vectors_checked": checked,
        "min_cosine_to_fresh_embedding": cos_min,
        "vectors_ok": cos_min >= COSINE_FLOOR,
    }


def _median(values: list[float]) -> float:
    return statistics.median(values)


def run(reps: int) -> dict[str, Any]:
    prepared = prepare(DEFAULT_DATASET)
    settings, ds = prepared.settings, prepared.dataset
    probes = [q.question for q in ds.questions]
    repository = ds.repository

    work = _fresh_dir(settings.data_dir / "live_work" / "flask")
    shutil.copytree(prepared.ingested.local_path, work, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(".git"))  # fmt: skip
    state = _fresh_dir(settings.data_dir / "live_state" / "flask")
    ref_state = settings.data_dir / "live_state" / "flask_ref"
    q_main = _fresh_dir(settings.data_dir / "qdrant_live_bench")
    q_ref = _fresh_dir(settings.data_dir / "qdrant_live_ref")

    embedder = get_embedder(settings)
    embedder.embed_documents(["warm-up text"] * 4)
    embedder.embed_query("warm-up query")

    report: dict[str, Any] = {
        "plan": "evaluation/plans/m11-incremental-indexing.md",
        "repository": repository,
        "commit": ds.commit,
        "dataset_fingerprint": ds.questions_sha256,
        "embedding_model": embedder.model_name,
        "device": embedder.device,
        "repetitions": reps,
        "environment": environment(("sentence-transformers", "torch", "qdrant-client", "networkx")),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }

    with VectorStore(q_main) as store, VectorStore(q_ref) as ref_store:
        size, overlap = settings.baseline_chunk_size, settings.baseline_chunk_overlap
        index = LiveIndex(repository, work, state, store, embedder, size, overlap)
        print("cold build ...", flush=True)
        cold = index.build_full()
        report["cold_build"] = {
            "files": cold.added,
            "chunks": {v: cold.views[v].new_chunks for v in VIEWS},
            "seconds": {k: round(v, 3) for k, v in cold.seconds.items()},
            "views": _view_summary(cold),
        }
        cold_total = cold.seconds["total"]
        print(f"  cold build: {cold_total:.1f}s", flush=True)

        base = index.snapshot(probes)
        report["m8_rankings_at_start"] = check_m8_rankings(index, ds.questions, RESULTS_DIR)
        print(
            "  reproduces the saved Milestone 8 rankings:",
            report["m8_rankings_at_start"]["identical"],
            flush=True,
        )

        scenarios: list[dict[str, Any]] = []
        for sc in SCENARIOS:
            print(f"scenario {sc.id} ...", flush=True)
            captured = _capture(work, sc.paths)
            runs: list[UpdateStats] = []
            check: dict[str, Any] = {}
            edge: dict[str, Any] | None = None
            restored_ok = True
            for rep in range(reps):
                sc.mutate(work)
                if sc.edge_disappears and rep == 0:
                    assert index.graph is not None
                    present_before = index.graph.has_edge("calls", *sc.edge_disappears)
                stats = index.update()
                runs.append(stats)
                if rep == 0:
                    check = verify(index, probes, ref_store, ref_state, embedder, sc.paths, seed=1)
                    if sc.edge_disappears:
                        assert index.graph is not None
                        edge = {
                            "edge": list(sc.edge_disappears),
                            "present_before": present_before,
                            "present_after": index.graph.has_edge("calls", *sc.edge_disappears),
                        }
                _restore(work, captured)
                index.update()  # back to the base state (not timed)
                restored_ok = restored_ok and index.snapshot(probes) == base

            totals = [r.seconds["total"] for r in runs]
            median_run = sorted(runs, key=lambda r: r.seconds["total"])[len(runs) // 2]
            scenarios.append(
                {
                    "id": sc.id,
                    "description": sc.description,
                    "files": {
                        k: getattr(median_run, k)
                        for k in ("added", "modified", "deleted", "unchanged")
                    },
                    "views": _view_summary(median_run),
                    "graph_edges_added": median_run.graph_edges_added,
                    "graph_edges_removed": median_run.graph_edges_removed,
                    "seconds_median": round(_median(totals), 4),
                    "seconds_min": round(min(totals), 4),
                    "seconds_max": round(max(totals), 4),
                    "speedup_vs_cold_build": round(cold_total / _median(totals), 1),
                    "breakdown_median_run": {k: round(v, 4) for k, v in median_run.seconds.items()},
                    "verification": check,
                    "graph_edge_check": edge,
                    "restored_to_base": restored_ok,
                }
            )
            speedup = cold_total / _median(totals)
            same = check.get("identical_to_full_rebuild", "n/a (nothing changed)")
            print(
                f"  median {_median(totals):.3f}s ({speedup:.0f}x faster than the cold build); "
                f"identical to full rebuild: {same}",
                flush=True,
            )
        report["scenarios"] = scenarios
        report["m8_rankings_at_end"] = check_m8_rankings(index, ds.questions, RESULTS_DIR)

    ok = (
        report["m8_rankings_at_start"]["identical"]
        and report["m8_rankings_at_end"]["identical"]
        and all(s["restored_to_base"] for s in scenarios)
        and all(
            s["verification"].get("identical_to_full_rebuild", True)
            and s["verification"].get("vectors_ok", True)
            for s in scenarios
        )
        and all(
            s["graph_edge_check"]["present_before"] and not s["graph_edge_check"]["present_after"]
            for s in scenarios
            if s["graph_edge_check"]
        )
    )
    report["all_criteria_hold"] = ok
    return report


def format_report(report: dict[str, Any]) -> str:
    cold = report["cold_build"]
    sec = cold["seconds"]
    lines = [
        f"Cold build: {sec['total']:.1f}s  (files {cold['files']}, chunks {cold['chunks']}; "
        f"embed {sec.get('embed', 0):.1f}s, parse+chunk {sec.get('parse_chunk', 0):.2f}s, "
        f"graph {sec.get('graph', 0):.2f}s, bm25 {sec.get('bm25', 0):.2f}s)",
        "",
        f"{'scenario':<22}{'files +/~/-':>12}{'median s':>10}{'speedup':>9}  "
        f"{'embedded (fixed/ast)':>22}{'reused (fixed/ast)':>20}  identical",
    ]
    for s in report["scenarios"]:
        f = s["files"]
        v = s["views"]
        ident = s["verification"].get("identical_to_full_rebuild", "-")
        lines.append(
            f"{s['id']:<22}{f['added']:>4}/{f['modified']}/{f['deleted']:<5}{s['seconds_median']:>10.3f}"
            f"{s['speedup_vs_cold_build']:>8.0f}x  "
            f"{v['fixed']['embedded_chunks']:>10} /{v['ast']['embedded_chunks']:>8}"
            f"{v['fixed']['reused_chunks']:>12} /{v['ast']['reused_chunks']:>6}   {ident}"
        )
    lines += [
        "",
        f"M8 rankings reproduced at start: {report['m8_rankings_at_start']['identical']}, "
        f"at end: {report['m8_rankings_at_end']['identical']}",
        f"ALL CORRECTNESS CRITERIA HOLD: {report['all_criteria_hold']}",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.evaluation.run_incremental", description=__doc__
    )
    parser.add_argument("--reps", type=int, default=3)
    args = parser.parse_args(argv)
    try:
        report = run(args.reps)
    except ExperimentError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("\n" + format_report(report))
    print(f"\nSaved to {OUT}")
    return 0 if report["all_criteria_hold"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
