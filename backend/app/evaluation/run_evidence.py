"""Milestone 12: validate the Evidence API on Flask. Invariants only: no accuracy metric.

    python -m app.evaluation.run_evidence

Plan: evaluation/plans/m12-evidence-api.md (section 10, plus the three recorded deviations). The 73
frozen questions are used purely as realistic queries; nothing is scored or tuned.
"""

import json
import shutil
import subprocess
import sys
import time
from collections import Counter
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
from app.evaluation.run_incremental import SCENARIOS
from app.evidence.builder import EvidenceBuilder
from app.evidence.schema import EvidenceResponse
from app.evidence.service import open_live_index
from app.evidence.validate import check_response, compare_evidence
from app.indexing.live_index import LiveIndex
from app.retrieval.embedder import SeededEmbedder
from app.retrieval.hybrid import HybridRetriever
from app.retrieval.vector_store import VectorStore

OUT = RESULTS_DIR / "evidence_api_flask.json"
SCHEMA_OUT = RESULTS_DIR / "evidence_schema_v1.json"
REPO_NAME = "flask_evidence"
TOP_K = 10  # the API default, and the length of the saved M8 rankings
FRESH_PROCESS_SAMPLE = 10  # questions re-run through the command line in a new process
EQUIVALENCE_SCENARIOS = ("edit_function", "graph_rename_symbol", "add_file")

# Chosen before any output was seen, from Flask's documentation rather than the benchmark.
SPOT_CHECK_QUERIES = [
    "save_session",
    "Blueprint register",
    "dispatch_request",
    "url_for",
    "make_response",
    "teardown_appcontext",
    "render_template",
    "jsonify",
    "register_error_handler",
    "test_client",
]


def _fresh_dir(path: Path) -> Path:
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path


def _copy_tree(src: Path, dst: Path) -> Path:
    _fresh_dir(dst)
    shutil.copytree(src, dst, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git"))
    return dst


def _embedding_ties(index: LiveIndex, query: str) -> bool:
    """Does the query's depth-50 embedding list contain exactly equal scores?"""
    scores = [h.score for h in index.embedding_search("ast")(query, RETRIEVAL_DEPTH)]
    return len(set(scores)) != len(scores)


def _source_line(root: Path, file: str, line: int | None) -> str | None:
    if line is None:
        return None
    lines = (root / file).read_text(encoding="utf-8", errors="replace").split("\n")
    return lines[line - 1].strip() if 1 <= line <= len(lines) else None


def spot_check_dump(builder: EvidenceBuilder, root: Path, query: str) -> dict[str, Any]:
    """What the top items claim, next to the source text at the cited lines, for hand checking."""
    response = builder.build(query, top_k=3)
    items = []
    for item in response.evidence:
        graph = item.graph
        edges = []
        for rel in graph.relationships[:3] if graph else []:
            # the cited line is in the file where the statement is: the caller's or importer's
            in_this_file = rel.relationship in ("calls", "imports", "inherits")
            where = item.file if in_this_file else rel.other_file
            edges.append(
                {
                    "relationship": rel.relationship,
                    "other": rel.other,
                    "rule": rel.rule,
                    "cited_file": where,
                    "cited_line": rel.line,
                    "source_at_cited_line": _source_line(root, where, rel.line),
                }
            )
        items.append(
            {
                "rank": item.rank,
                "file": item.file,
                "lines": [item.line_start, item.line_end],
                "symbol": item.symbol.qualified_name if item.symbol else None,
                "symbol_status": item.symbol_status,
                "source_at_line_start": _source_line(root, item.file, item.line_start),
                "graph_edges": edges,
            }
        )
    return {"query": query, "items": items}


def _fresh_process(question: str, root: Path, revision: str) -> EvidenceResponse:
    done = subprocess.run(
        [sys.executable, "-m", "app.evidence", "--repository", REPO_NAME, "--root", str(root),
         "--revision", revision, "--top-k", str(TOP_K), "--text", question],
        capture_output=True, text=True, check=True, encoding="utf-8",
    )  # fmt: skip
    return EvidenceResponse.model_validate_json(done.stdout)


def main_checks(builder: EvidenceBuilder, root: Path, ds: Any, out_dir: Path) -> dict[str, Any]:
    """Checks 1-6 over the 73 questions, on the real index."""
    index = builder.index
    saved = json.loads((out_dir / HYBRID_RESULT_FILES[("ast", "hybrid")]).read_text("utf-8"))
    saved_files = {q["id"]: q["retrieved_files"] for q in saved["per_question"]}
    plain = HybridRetriever([index.bm25_search("ast"), index.embedding_search("ast")])

    order_vs_m8: list[str] = []
    vs_plain: list[str] = []
    violations: dict[str, list[str]] = {}
    repeat_diffs: dict[str, list[str]] = {}
    statuses: Counter[str] = Counter()
    categories: Counter[str] = Counter()
    graph_stats = Counter()
    tie_questions: list[str] = []
    responses: dict[str, EvidenceResponse] = {}
    for q in ds.questions:
        response = builder.build(q.question, top_k=TOP_K, include_text=True)
        responses[q.id] = response
        files = [i.file for i in response.evidence]
        if files != saved_files[q.id]:
            order_vs_m8.append(q.id)
        baseline = plain.search(q.question, RETRIEVAL_DEPTH)[:TOP_K]
        same = [
            (i.file, i.line_start, i.line_end, round(i.scores.rrf, 6)) for i in response.evidence
        ] == [(h.file_path, h.start_line, h.end_line, round(h.score, 6)) for h in baseline]
        if not same:
            vs_plain.append(q.id)
        problems = check_response(response, index, root)
        if problems:
            violations[q.id] = problems
        again = builder.build(q.question, top_k=TOP_K, include_text=True)
        diffs = compare_evidence(response, again)
        if diffs:
            repeat_diffs[q.id] = diffs
        if _embedding_ties(index, q.question):
            tie_questions.append(q.id)
        for item in response.evidence:
            statuses[item.symbol_status] += 1
            categories[item.category] += 1
            if item.graph:
                graph_stats["items_with_graph"] += 1
                graph_stats["items_with_relationships"] += bool(item.graph.relationships)
                graph_stats["items_truncated"] += item.graph.truncated
                graph_stats["links_to_other_results"] += len(item.graph.links_to_other_results)
    n_items = sum(len(r.evidence) for r in responses.values())
    return {
        "questions": len(ds.questions),
        "items": n_items,
        "check1_file_order_equals_saved_m8_top10": {"mismatches": order_vs_m8},
        "check1_equals_unmodified_hybrid_retriever": {"mismatches": vs_plain},
        "checks2to4_6_invariant_violations": violations,
        "check5_repeat_in_process": {"questions_with_differences": repeat_diffs},
        "symbol_status_counts": dict(statuses),
        "category_counts": dict(categories),
        "graph": dict(graph_stats),
        "questions_with_exact_embedding_ties": tie_questions,
        "_responses": responses,
    }


def equivalence_at_scale(base_tree: Path, scratch: Path, ds: Any) -> dict[str, Any]:
    """Check 8 on Flask (deviation 3): evidence after an incremental update vs a fresh build."""
    results: dict[str, Any] = {}
    by_id = {s.id: s for s in SCENARIOS}
    for sid in EQUIVALENCE_SCENARIOS:
        tree = _copy_tree(base_tree, scratch / f"tree_{sid}")
        with (
            VectorStore(_fresh_dir(scratch / f"qa_{sid}")) as store_a,
            VectorStore(_fresh_dir(scratch / f"qb_{sid}")) as store_b,
        ):
            live = LiveIndex(REPO_NAME, tree, _fresh_dir(scratch / f"sa_{sid}"), store_a,
                             SeededEmbedder(), 1000, 200)  # fmt: skip
            live.build_full()
            by_id[sid].mutate(tree)
            live.update()
            fresh = LiveIndex(REPO_NAME, tree, _fresh_dir(scratch / f"sb_{sid}"), store_b,
                              SeededEmbedder(), 1000, 200)  # fmt: skip
            fresh.build_full()
            a, b = EvidenceBuilder(live, "x"), EvidenceBuilder(fresh, "x")
            identical, different, explained, unexplained, invalid = 0, [], [], [], []
            for q in ds.questions:
                ra = a.build(q.question, top_k=TOP_K, include_text=True)
                rb = b.build(q.question, top_k=TOP_K, include_text=True)
                if check_response(ra, live, tree):
                    invalid.append(q.id)
                if not compare_evidence(ra, rb):
                    identical += 1
                    continue
                different.append(q.id)
                (explained if _embedding_ties(live, q.question) else unexplained).append(q.id)
            results[sid] = {
                "questions": len(ds.questions),
                "identical": identical,
                "different": different,
                "different_with_exact_embedding_ties": explained,
                "different_without_ties": unexplained,
                "invalid_responses": invalid,
                "passes": not unexplained and not invalid,
            }
            print(
                f"  {sid}: identical {identical}/{len(ds.questions)}, "
                f"different {len(different)} (unexplained {len(unexplained)})",
                flush=True,
            )
    return results


def run() -> dict[str, Any]:
    prepared = prepare(DEFAULT_DATASET)
    settings, ds = prepared.settings, prepared.dataset
    work = _copy_tree(prepared.ingested.local_path, settings.data_dir / "live_work" / REPO_NAME)
    shutil.rmtree(settings.live_state_dir(REPO_NAME), ignore_errors=True)
    shutil.rmtree(settings.live_qdrant_path, ignore_errors=True)

    report: dict[str, Any] = {
        "plan": "evaluation/plans/m12-evidence-api.md",
        "repository": ds.repository,
        "commit": ds.commit,
        "dataset_fingerprint": ds.questions_sha256,
        "top_k": TOP_K,
        "environment": environment(("sentence-transformers", "qdrant-client", "networkx")),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    SCHEMA_OUT.write_text(
        json.dumps(EvidenceResponse.model_json_schema(), indent=1) + "\n", encoding="utf-8"
    )

    from app.retrieval.embedder import get_embedder

    embedder = get_embedder(settings)
    embedder.embed_documents(["warm-up"] * 4)
    embedder.embed_query("warm-up")
    index, store = open_live_index(settings, REPO_NAME, work, embedder)
    try:
        print("building the live index ...", flush=True)
        cold = index.build_full()
        report["index_build_seconds"] = round(cold.seconds["total"], 1)
        builder = EvidenceBuilder(index, ds.commit)

        print("checks 1-6 over the questions ...", flush=True)
        main = main_checks(builder, work, ds, RESULTS_DIR)
        responses = main.pop("_responses")

        latencies = []
        for q in ds.questions[:30]:
            t = time.perf_counter()
            builder.build(q.question, top_k=TOP_K)
            latencies.append((time.perf_counter() - t) * 1000)
        latencies.sort()
        report["latency_ms_top10_no_text"] = {
            "median": round(latencies[len(latencies) // 2], 1),
            "max": round(latencies[-1], 1),
            "n": len(latencies),
        }

        report["spot_checks"] = [spot_check_dump(builder, work, q) for q in SPOT_CHECK_QUERIES]
    finally:
        store.close()

    print("fresh-process determinism ...", flush=True)
    sample = ds.questions[:: max(1, len(ds.questions) // FRESH_PROCESS_SAMPLE)][
        :FRESH_PROCESS_SAMPLE
    ]
    fresh_diffs = {}
    for q in sample:
        diffs = compare_evidence(responses[q.id], _fresh_process(q.question, work, ds.commit))
        if diffs:
            fresh_diffs[q.id] = diffs
    main["check5_fresh_process"] = {"questions": [q.id for q in sample], "differences": fresh_diffs}

    print("check 8 at Flask scale ...", flush=True)
    scratch = _fresh_dir(settings.data_dir / "live_work" / "flask_evidence_eq")
    main["check8_incremental_equals_fresh"] = equivalence_at_scale(work, scratch, ds)
    shutil.rmtree(scratch, ignore_errors=True)

    report["checks"] = main
    report["passes"] = (
        not main["check1_file_order_equals_saved_m8_top10"]["mismatches"]
        and not main["check1_equals_unmodified_hybrid_retriever"]["mismatches"]
        and not main["checks2to4_6_invariant_violations"]
        and not main["check5_repeat_in_process"]["questions_with_differences"]
        and not main["check5_fresh_process"]["differences"]
        and all(r["passes"] for r in main["check8_incremental_equals_fresh"].values())
    )
    return report


def main() -> int:
    try:
        report = run()
    except ExperimentError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    OUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    c = report["checks"]
    m8 = c["check1_file_order_equals_saved_m8_top10"]["mismatches"]
    plain = c["check1_equals_unmodified_hybrid_retriever"]["mismatches"]
    repeat = c["check5_repeat_in_process"]["questions_with_differences"]
    lines = [
        f"index build {report['index_build_seconds']}s; "
        f"latency {report['latency_ms_top10_no_text']}",
        f"check 1, mismatches vs saved M8 order: {m8}",
        f"check 1, mismatches vs plain retriever: {plain}",
        f"checks 2-4,6, questions with violations: {len(c['checks2to4_6_invariant_violations'])}",
        f"check 5, in-process differences: {len(repeat)}",
        f"check 5, fresh-process differences: {len(c['check5_fresh_process']['differences'])}",
        f"symbol status: {c['symbol_status_counts']}; graph: {c['graph']}",
        f"questions with exact embedding ties: {len(c['questions_with_exact_embedding_ties'])}",
    ]
    for sid, r in c["check8_incremental_equals_fresh"].items():
        lines.append(
            f"check 8 {sid}: identical {r['identical']}/{r['questions']}, "
            f"unexplained {len(r['different_without_ties'])}"
        )
    lines.append(f"ALL CHECKS PASS: {report['passes']}")
    print("\n" + "\n".join(lines) + f"\nSaved to {OUT}")
    return 0 if report["passes"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
