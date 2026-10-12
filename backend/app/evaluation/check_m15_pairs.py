"""Milestone 15: authoring checks for the false-premise question pairs (no model is called).

    python -m app.evaluation.check_m15_pairs

Reads evaluation/datasets/m15_pair_candidates.json, runs every automated check on every candidate
pair, selects the test and development pairs by a fixed rule, and writes the two dataset files and
a quality report. Checks (plan section 2 and the user's authoring rules):

  C1 format        both questions end in '?', 25-160 characters, no duplicates, none equal to an
                   existing benchmark or set-B question
  C2 symmetry      the pair shares at least two content words and the lengths are within 0.6-1.6x
  C3 absence       none of the premise's regexes matches any Python file under src/, tests/ or
                   examples/ of the pinned checkout
  C4 truth         the true-premise twin's file and symbol exist in the stored parse
  C5 availability  the v2 context of the twin contains that symbol's chunk
  C6 decoy         (S2/S3) the decoy symbol exists and is in the v2 context of the false-premise
                   question

Selection (fixed): per subtype, in candidate order, the first 8 passing pairs are the test set, the
next 3 (S1), 3 (S2), 2 (S3) the development set. Nothing here looks at any model output.
"""

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from app.evaluation.experiment import DEFAULT_DATASET, REPO_ROOT, RESULTS_DIR, prepare
from app.evaluation.run_answers import REPO_NAME, UNANSWERABLE
from app.evaluation.run_answers_v2 import context_has_symbol
from app.evidence.service import load_builder

CANDIDATES = REPO_ROOT / "evaluation" / "datasets" / "m15_pair_candidates.json"
TEST_OUT = REPO_ROOT / "evaluation" / "datasets" / "m15_pairs_test.json"
DEV_OUT = REPO_ROOT / "evaluation" / "datasets" / "m15_pairs_dev.json"
QUALITY_OUT = RESULTS_DIR / "m15_pair_quality.json"

TEST_PER_SUBTYPE = 8
DEV_PER_SUBTYPE = {"S1": 3, "S2": 3, "S3": 2}
TOP_K, EXTRA = 8, 2  # the M14 evidence mode, unchanged
MIN_SHARED = 2
LENGTH_RATIO = (0.6, 1.6)
MIN_CHARS, MAX_CHARS = 25, 160

STOPWORDS = frozenset(
    "a an the of in on at to for from by with and or is are does do did how where which what when "
    "who "
    "why it its this that these those be as into onto per via not no".split()
)


def normalise(question: str) -> str:
    return re.sub(r"\s+", " ", question.lower()).strip()


def content_tokens(question: str) -> set[str]:
    """Lower-case content words: stopwords, 'flask' and short words removed; a plural 's' and a
    possessive 's are stripped so 'sessions' and 'session' match."""
    tokens = set()
    for raw in re.findall(r"[a-z0-9_-]+", question.lower().replace("'s", "")):
        if raw in STOPWORDS or raw == "flask":
            continue
        word = raw[:-1] if len(raw) > 3 and raw.endswith("s") else raw
        if len(word) >= 3 and word not in STOPWORDS:
            tokens.add(word)
    return tokens


def check_format(question: str) -> list[str]:
    bad = []
    if not question.endswith("?"):
        bad.append("does not end with '?'")
    if not MIN_CHARS <= len(question) <= MAX_CHARS:
        bad.append(f"length {len(question)} outside {MIN_CHARS}-{MAX_CHARS}")
    return bad


def check_symmetry(p: str, q: str) -> list[str]:
    bad = []
    shared = content_tokens(p) & content_tokens(q)
    if len(shared) < MIN_SHARED:
        bad.append(f"shares {len(shared)} content words ({sorted(shared)}), needs {MIN_SHARED}")
    ratio = len(p) / len(q)
    if not LENGTH_RATIO[0] <= ratio <= LENGTH_RATIO[1]:
        bad.append(f"length ratio {ratio:.2f} outside {LENGTH_RATIO}")
    return bad


ABSENCE_SCOPE = ("src/", "tests/", "examples/")  # docs/conf.py is Sphinx configuration, not Flask


def python_texts(root: Path) -> dict[str, str]:
    texts = {
        str(p.relative_to(root)).replace("\\", "/"): p.read_text(encoding="utf-8", errors="replace")
        for p in sorted(root.rglob("*.py"))
        if ".git" not in p.parts
    }
    return {path: text for path, text in texts.items() if path.startswith(ABSENCE_SCOPE)}


def check_absence(patterns: list[str], texts: dict[str, str]) -> list[str]:
    """Regexes that must match NO Python file; each hit is reported with its file."""
    bad = []
    for pattern in patterns:
        rx = re.compile(pattern, re.IGNORECASE | re.MULTILINE)
        hits = [path for path, text in texts.items() if rx.search(text)]
        if hits:
            bad.append(f"/{pattern}/ occurs in {hits[:3]}")
    return bad


def symbol_exists(index: Any, file: str, name: str) -> bool:
    record = index.records.get(file)
    return bool(record) and any(s.qualified_name == name for s in record.parsed.symbols)


def check_candidate(
    c: dict[str, Any], builder: Any, texts: dict[str, str], existing: set[str]
) -> dict[str, Any]:
    problems: dict[str, list[str]] = {}

    def add(name: str, bad: list[str]) -> None:
        if bad:
            problems[name] = bad

    for key in ("p", "q"):
        add(f"C1_format_{key}", check_format(c[key]))
        if normalise(c[key]) in existing:
            add(f"C1_duplicate_{key}", ["equals an existing question"])
    add("C2_symmetry", check_symmetry(c["p"], c["q"]))
    add("C3_absence", check_absence(c["absent"], texts))

    truth = c["q_truth"]
    if not symbol_exists(builder.index, truth["file"], truth["name"]):
        add("C4_truth_exists", [f"{truth['file']}::{truth['name']} is not in the stored parse"])
    else:
        ev_q = builder.build_v2(c["q"], TOP_K, True, EXTRA)
        if not context_has_symbol(ev_q, [truth], include_additional=True):
            add("C5_truth_in_context", [f"{truth['name']} is not in the v2 context of the twin"])
    if c["subtype"] in ("S2", "S3") and not c["decoy"]:
        add("C6_decoy_required", ["S2 and S3 pairs need a decoy symbol"])
    if c["decoy"]:
        d = c["decoy"]
        if not symbol_exists(builder.index, d["file"], d["name"]):
            add("C6_decoy_exists", [f"{d['file']}::{d['name']} is not in the stored parse"])
        else:
            ev_p = builder.build_v2(c["p"], TOP_K, True, EXTRA)
            if not context_has_symbol(ev_p, [d], include_additional=True):
                add(
                    "C6_decoy_in_context",
                    [f"{d['name']} is not in the v2 context of the false-premise question"],
                )
    return {"id": c["id"], "subtype": c["subtype"], "passes": not problems, "problems": problems}


def select(results: list[dict[str, Any]]) -> tuple[list[str], list[str], dict[str, str]]:
    """Per subtype and in candidate order: first 8 passing -> test, next quota -> dev."""
    test: list[str] = []
    dev: list[str] = []
    shortfalls: dict[str, str] = {}
    for subtype, dev_quota in DEV_PER_SUBTYPE.items():
        passing = [r["id"] for r in results if r["subtype"] == subtype and r["passes"]]
        test += passing[:TEST_PER_SUBTYPE]
        dev += passing[TEST_PER_SUBTYPE : TEST_PER_SUBTYPE + dev_quota]
        need = TEST_PER_SUBTYPE + dev_quota
        if len(passing) < need:
            shortfalls[subtype] = f"{len(passing)} passing candidates, {need} needed"
    return test, dev, shortfalls


def dataset(ids: list[str], by_id: dict[str, dict[str, Any]], prefix: str) -> list[dict[str, Any]]:
    pairs = []
    for n, cid in enumerate(ids, 1):
        c = by_id[cid]
        pairs.append(
            {
                "pair_id": f"{prefix}{n:02d}",
                "candidate": cid,
                "subtype": c["subtype"],
                "p": {
                    "id": f"{prefix}{n:02d}-P",
                    "question": c["p"],
                    "false_premise": c["premise"],
                },
                "q": {"id": f"{prefix}{n:02d}-Q", "question": c["q"], "truth": c["q_truth"]},
                "decoy": c["decoy"],
                "absent_patterns": c["absent"],
            }
        )
    return pairs


CUE_WORDS = ("built-in",)
MAX_CUE_IMBALANCE = 2


def cue_balance(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    """C7: how often a cue word appears in P but not in its twin, and the reverse."""
    out: dict[str, Any] = {}
    for cue in CUE_WORDS:
        p_only = sum(
            1
            for x in pairs
            if cue in x["p"]["question"].lower() and cue not in x["q"]["question"].lower()
        )
        q_only = sum(
            1
            for x in pairs
            if cue in x["q"]["question"].lower() and cue not in x["p"]["question"].lower()
        )
        out[cue] = {
            "p_only": p_only,
            "q_only": q_only,
            "balanced": abs(p_only - q_only) <= MAX_CUE_IMBALANCE,
        }
    return out


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def main() -> int:
    prepared = prepare(DEFAULT_DATASET)
    settings, ds = prepared.settings, prepared.dataset
    root = settings.data_dir / "live_work" / REPO_NAME
    candidates = json.loads(CANDIDATES.read_text(encoding="utf-8"))["candidates"]
    by_id = {c["id"]: c for c in candidates}
    assert len(by_id) == len(candidates), "duplicate candidate ids"

    existing = {normalise(q.question) for q in ds.questions}
    existing |= {
        normalise(q["question"]) for q in json.loads(UNANSWERABLE.read_text("utf-8"))["questions"]
    }
    texts = python_texts(root)
    builder, store = load_builder(settings, REPO_NAME, root, ds.commit)
    try:
        results = [check_candidate(c, builder, texts, existing) for c in candidates]
    finally:
        store.close()

    # duplicates inside the pool itself
    seen: dict[str, str] = {}
    for c in candidates:
        for key in ("p", "q"):
            n = normalise(c[key])
            if n in seen and seen[n] != c["id"]:
                for r in results:
                    if r["id"] == c["id"]:
                        r["passes"] = False
                        r["problems"]["C1_duplicate_in_pool"] = [
                            f"{key} repeats a question of {seen[n]}"
                        ]
            seen.setdefault(n, c["id"])

    test_ids, dev_ids, shortfalls = select(results)
    TEST_OUT.write_text(
        json.dumps(
            {
                "description": "M15 frozen test pairs (24): selected by automated checks only.",
                "repository": ds.repository,
                "commit": ds.commit,
                "pairs": dataset(test_ids, by_id, "t"),
            },
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )
    DEV_OUT.write_text(
        json.dumps(
            {
                "description": "M15 development pairs (8), to choose among prompt candidates only.",
                "repository": ds.repository,
                "commit": ds.commit,
                "known_failures": ["unans-08", "unans-14", "unans-11"],
                "pairs": dataset(dev_ids, by_id, "d"),
            },
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )

    by_subtype: dict[str, dict[str, int]] = {}
    for r in results:
        s = by_subtype.setdefault(r["subtype"], {"candidates": 0, "passing": 0})
        s["candidates"] += 1
        s["passing"] += r["passes"]
    failures_by_check: dict[str, int] = {}
    for r in results:
        for name in r["problems"]:
            failures_by_check[name] = failures_by_check.get(name, 0) + 1
    quality = {
        "candidates": len(candidates), "passing": sum(r["passes"] for r in results),
        "by_subtype": by_subtype, "failures_by_check": failures_by_check,
        "shortfalls": shortfalls, "selected_test": test_ids, "selected_dev": dev_ids,
        "rejected": [r for r in results if not r["passes"]],
        "C7_cue_balance": {
            "test": cue_balance(dataset(test_ids, by_id, "t")),
            "dev": cue_balance(dataset(dev_ids, by_id, "d")),
        },
        "files": {p.name: sha256_file(p) for p in (CANDIDATES, TEST_OUT, DEV_OUT)},
        "rules": {"min_shared_content_words": MIN_SHARED, "length_ratio": LENGTH_RATIO,
                  "evidence_mode": f"schema v2, top_k {TOP_K}, extra_chunks {EXTRA}"},
    }  # fmt: skip
    QUALITY_OUT.write_text(json.dumps(quality, indent=1) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                k: quality[k]
                for k in ("candidates", "passing", "by_subtype", "failures_by_check", "shortfalls")
            },
            indent=1,
        )
    )
    print(
        "selected test:",
        len(test_ids),
        "| dev:",
        len(dev_ids),
        "| C7 cue balance:",
        quality["C7_cue_balance"],
    )
    if not all(v["balanced"] for part in quality["C7_cue_balance"].values() for v in part.values()):
        shortfalls["C7"] = (
            "cue words are unbalanced between false-premise questions and their twins"
        )
    for r in quality["rejected"]:
        print(
            f"  REJECTED {r['id']}: " + "; ".join(f"{k}: {v[0]}" for k, v in r["problems"].items())
        )
    return 0 if not shortfalls else 2


if __name__ == "__main__":
    sys.exit(main())
