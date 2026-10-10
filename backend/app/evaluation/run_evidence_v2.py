"""Milestone 14: validate Evidence API schema v2 on Flask before any live model call (plan s.2).

    python -m app.evaluation.run_evidence_v2

Invariants (a)-(h), over the 73 benchmark questions as realistic queries (no accuracy metric):
(a) extra_chunks=0 equals v1 in every v1 field; (b) for N = 1, 2, 4 the files, primary chunks,
scores,
symbols and graph facts equal v1; (c)/(d)/(f) additional chunks are valid, distinct, bounded and
equal the file's lines (check_response_v2); (e) the selection equals an independent derivation from
the retrievers' chunk lists and repeats; (g) an incrementally updated index gives the same v2
evidence as a fresh one except for exactly tied embedding scores; (h) v1 still equals the saved
Milestone 8 file order and passes the Milestone 12 checks. Results go to a new file.
"""

import json
import shutil
import sys
from pathlib import Path
from typing import Any

from app.evaluation.experiment import DEFAULT_DATASET, RESULTS_DIR, ExperimentError, prepare
from app.evaluation.run_evidence import REPO_NAME, _copy_tree, _embedding_ties, _fresh_dir
from app.evaluation.run_hybrid import FILES as HYBRID_RESULT_FILES
from app.evaluation.run_incremental import SCENARIOS
from app.evidence.builder import DEPTH, EvidenceBuilder
from app.evidence.schema import EvidenceResponseV2
from app.evidence.service import load_builder
from app.evidence.validate import check_response, check_response_v2
from app.indexing.live_index import LiveIndex
from app.retrieval.embedder import SeededEmbedder
from app.retrieval.vector_store import VectorStore

OUT = RESULTS_DIR / "evidence_v2_validation_flask.json"
EQUIVALENCE_SCENARIOS = ("edit_function", "graph_rename_symbol", "add_file")
TOP_K = 10


def _v1_view(item: Any) -> dict[str, Any]:
    d = item.model_dump(mode="json")
    d.pop("additional_chunks", None)
    if d["scores"]["embedding"]:
        d["scores"]["embedding"]["score"] = None  # float32 noise of the vector store (M12)
    return d


def _shape(response: EvidenceResponseV2) -> list[Any]:
    return [
        (i.file, i.line_start, [(c.line_start, c.line_end, c.text) for c in i.additional_chunks])
        for i in response.evidence
    ]


def _derive(builder: EvidenceBuilder, query: str, response: EvidenceResponseV2) -> list[str]:
    """Plan (e): re-derive every file's additional chunks from the retrievers' chunk lists."""
    index = builder.index
    lists = [index.bm25_search("ast")(query, DEPTH), index.embedding_search("ast")(query, DEPTH)]
    bad = []
    for item in response.evidence:
        found: dict[tuple[int, int], list[int | None]] = {}
        for comp, hits in enumerate(lists):
            for pos, hit in enumerate(hits, 1):
                if hit.file_path == item.file:
                    found.setdefault((hit.start_line, hit.end_line), [None, None])[comp] = pos
        found.pop((item.line_start, item.line_end), None)

        def score(positions: list[int | None]) -> float:
            return sum(1 / (60 + p) for p in positions if p)

        ranked = sorted(
            found.items(),
            key=lambda kv: (-score(kv[1]), min(p for p in kv[1] if p), kv[0][0]),
        )
        expected = [span for span, _ in ranked[: response.extra_chunks]]
        got = [(c.line_start, c.line_end) for c in item.additional_chunks]
        if got != expected:
            bad.append(f"{item.file}:{item.line_start}: {got} != {expected}")
    return bad


def main_checks(builder: EvidenceBuilder, root: Path, ds: Any) -> dict[str, Any]:
    saved = json.loads((RESULTS_DIR / HYBRID_RESULT_FILES[("ast", "hybrid")]).read_text("utf-8"))
    saved_files = {q["id"]: q["retrieved_files"] for q in saved["per_question"]}
    failures: dict[str, dict[str, list[str]]] = {
        k: {} for k in ("a_zero_equals_v1", "b_files_unchanged", "cdf_invalid", "e_derivation",
                        "e_repeat", "h_v1_checks", "h_m8_order")
    }  # fmt: skip
    extras_total = files_with_extras = files_total = 0
    for q in ds.questions:
        v1 = builder.build(q.question, DEPTH, True)
        v1_view = [_v1_view(i) for i in v1.evidence]
        zero = builder.build_v2(q.question, DEPTH, True, 0)
        if [_v1_view(i) for i in zero.evidence] != v1_view or any(
            i.additional_chunks for i in zero.evidence
        ):
            failures["a_zero_equals_v1"][q.id] = ["differs"]
        for n in (1, 2, 4):
            v2 = builder.build_v2(q.question, DEPTH, True, n)
            if [_v1_view(i) for i in v2.evidence] != v1_view:
                failures["b_files_unchanged"].setdefault(q.id, []).append(f"N={n}")
            if problems := check_response_v2(v2, builder.index, root, n):
                failures["cdf_invalid"].setdefault(q.id, []).extend(problems[:3])
            if n == 2:
                if problems := _derive(builder, q.question, v2):
                    failures["e_derivation"][q.id] = problems[:3]
                again = builder.build_v2(q.question, DEPTH, True, n)
                if _shape(again) != _shape(v2):
                    failures["e_repeat"][q.id] = ["the two calls differ"]
                files_total += len(v2.evidence)
                files_with_extras += sum(1 for i in v2.evidence if i.additional_chunks)
                extras_total += sum(len(i.additional_chunks) for i in v2.evidence)
        if problems := check_response(builder.build(q.question, TOP_K, True), builder.index, root):
            failures["h_v1_checks"][q.id] = problems[:3]
        if [i.file for i in builder.build(q.question, TOP_K).evidence] != saved_files[q.id]:
            failures["h_m8_order"][q.id] = ["file order differs from the saved M8 ranking"]
    return {
        "questions": len(ds.questions),
        "failures": {k: v for k, v in failures.items()},
        "all_hold": not any(failures.values()),
        "n2_extra_chunks_total": extras_total,
        "n2_files_total": files_total,
        "n2_files_with_extra_chunks": files_with_extras,
    }


def equivalence_v2(base_tree: Path, scratch: Path, ds: Any) -> dict[str, Any]:
    """Invariant (g) at Flask scale: incremental update vs fresh build, with a seeded embedder."""
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
            identical, different, unexplained, invalid = 0, [], [], []
            for q in ds.questions:
                ra = a.build_v2(q.question, DEPTH, True, 2)
                rb = b.build_v2(q.question, DEPTH, True, 2)
                if check_response_v2(ra, live, tree, 2):
                    invalid.append(q.id)
                if _shape(ra) == _shape(rb):
                    identical += 1
                    continue
                different.append(q.id)
                if not _embedding_ties(live, q.question):
                    unexplained.append(q.id)
            results[sid] = {
                "identical": identical, "different": different,
                "different_without_ties": unexplained, "invalid_responses": invalid,
                "passes": not unexplained and not invalid,
            }  # fmt: skip
            print(
                f"  {sid}: identical {identical}/{len(ds.questions)}, "
                f"unexplained {len(unexplained)}",
                flush=True,
            )
    return results


def main() -> int:
    try:
        prepared = prepare(DEFAULT_DATASET)
    except ExperimentError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    settings, ds = prepared.settings, prepared.dataset
    root = settings.data_dir / "live_work" / REPO_NAME
    builder, store = load_builder(settings, REPO_NAME, root, ds.commit)
    try:
        print("invariants (a)-(f), (h) over the questions ...", flush=True)
        main_result = main_checks(builder, root, ds)
    finally:
        store.close()
    print("invariant (g), incremental vs fresh ...", flush=True)
    scratch = _fresh_dir(settings.data_dir / "live_work" / "flask_evidence_v2_eq")
    equivalence = equivalence_v2(root, scratch, ds)
    shutil.rmtree(scratch, ignore_errors=True)
    report = {
        "plan": "evaluation/plans/m14-additional-chunks.md",
        "commit": ds.commit,
        "dataset_fingerprint": ds.questions_sha256,
        "invariants_a_to_f_and_h": main_result,
        "invariant_g_incremental": equivalence,
        "passes": main_result["all_hold"] and all(r["passes"] for r in equivalence.values()),
    }
    OUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps({k: report[k] for k in ("passes",)}),
        "|",
        {k: len(v) for k, v in main_result["failures"].items()},
    )
    print(
        "n=2:",
        main_result["n2_extra_chunks_total"],
        "extra chunks over",
        main_result["n2_files_total"],
        "files;",
        main_result["n2_files_with_extra_chunks"],
        "files have at least one",
    )
    print(f"Saved to {OUT}")
    return 0 if report["passes"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
