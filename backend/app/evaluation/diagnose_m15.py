"""Model-free diagnostic of the frozen M15 test pairs (no model call, no change to any question).

    python -m app.evaluation.diagnose_m15

For each pair: is an M14 baseline answer available for either question, does the false-premise
question's v2 context contain the assumed feature (direct evidence, by pattern), the twin's answer
or a decoy (related-only evidence), and does the twin's context contain its answer chunk.
"""

import json
from collections import Counter

from app.evaluation import run_answers as ra
from app.evaluation.check_m15_pairs import (
    EXTRA,
    TOP_K,
    normalise,
    python_texts,
)
from app.evaluation.experiment import DEFAULT_DATASET, REPO_ROOT, RESULTS_DIR, prepare
from app.evaluation.run_answers_v2 import context_has_symbol
from app.evidence.service import load_builder

TEST = REPO_ROOT / "evaluation" / "datasets" / "m15_pairs_test.json"
OUT = RESULTS_DIR / "m15_test_diagnostic.json"
M14_FILES = ("answers_flask_raw.jsonl", "answers_v2_raw.jsonl", "answers_v1_repeat_raw.jsonl")


def m14_questions() -> set[str]:
    found: set[str] = set()
    for name in M14_FILES:
        for line in (RESULTS_DIR / name).read_text(encoding="utf-8").splitlines():
            if line:
                found.add(normalise(json.loads(line)["answer"]["question"])) if (
                    "answer" in json.loads(line)
                ) else None
    return found


def main() -> int:
    prepared = prepare(DEFAULT_DATASET)
    settings, ds = prepared.settings, prepared.dataset
    root = settings.data_dir / "live_work" / ra.REPO_NAME
    pairs = json.loads(TEST.read_text(encoding="utf-8"))["pairs"]
    texts = python_texts(root)
    known = m14_questions()
    builder, store = load_builder(settings, ra.REPO_NAME, root, ds.commit)
    rows = []
    try:
        for pair in pairs:
            p, q = pair["p"]["question"], pair["q"]["question"]
            ev_p = builder.build_v2(p, TOP_K, True, EXTRA)
            ev_q = builder.build_v2(q, TOP_K, True, EXTRA)
            truth, decoy = pair["q"]["truth"], pair["decoy"]
            direct = [pat for pat in pair["absent_patterns"] if _hits(pat, texts)]
            rows.append(
                {
                    "pair": pair["pair_id"],
                    "subtype": pair["subtype"],
                    "m14_answer_for_p": normalise(p) in known,
                    "m14_answer_for_q": normalise(q) in known,
                    "assumed_feature_in_repo_source": direct,
                    "p_context_has_twin_answer": context_has_symbol(
                        ev_p, [truth], include_additional=True
                    ),
                    "p_context_has_decoy": bool(decoy)
                    and context_has_symbol(ev_p, [decoy], include_additional=True),
                    "q_context_has_answer": context_has_symbol(
                        ev_q, [truth], include_additional=True
                    ),
                }
            )
    finally:
        store.close()
    for r in rows:
        r["p_related_only"] = (
            r["p_context_has_twin_answer"] or r["p_context_has_decoy"]
        ) and not r["assumed_feature_in_repo_source"]
    count = Counter()
    for r in rows:
        count["pairs"] += 1
        count["m14_answer_for_p"] += r["m14_answer_for_p"]
        count["m14_answer_for_q"] += r["m14_answer_for_q"]
        count["p_direct_evidence_exists"] += bool(r["assumed_feature_in_repo_source"])
        count["p_context_has_twin_answer"] += r["p_context_has_twin_answer"]
        count["p_context_has_decoy"] += r["p_context_has_decoy"]
        count["p_related_only"] += r["p_related_only"]
        count["q_context_has_answer"] += r["q_context_has_answer"]
        count[f"related_only_{r['subtype']}"] += r["p_related_only"]
        count[f"pairs_{r['subtype']}"] += 1
    OUT.write_text(json.dumps({"summary": count, "pairs": rows}, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(count, indent=1))
    return 0


def _hits(pattern: str, texts: dict[str, str]) -> bool:
    import re

    rx = re.compile(pattern, re.IGNORECASE)
    return any(rx.search(t) for t in texts.values())


if __name__ == "__main__":
    raise SystemExit(main())
