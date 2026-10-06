"""Check that an evaluation dataset is well-formed and still true of the pinned checkout.

    python -m app.evaluation.validate ../evaluation/datasets/flask.json [--repo-path DIR]

Exit status is 1 if any problem is found, so this can run in CI.
"""

import argparse
import subprocess
import sys
from collections import Counter
from pathlib import Path

from app.config import get_settings
from app.evaluation.dataset import (
    EvalDataset,
    compute_questions_sha256,
    expected_difficulty,
    load_dataset,
)
from app.parsing.python_parser import parse_python_file


def check_structure(ds: EvalDataset) -> list[str]:
    """Problems that can be found without the repository checkout."""
    errors: list[str] = []
    for label, values in (
        ("id", [q.id for q in ds.questions]),
        ("question", [q.question.strip().lower() for q in ds.questions]),
    ):
        for value, n in Counter(values).items():
            if n > 1:
                errors.append(f"duplicate {label}: {value!r} ({n} times)")

    for q in ds.questions:
        files = q.relevant_files
        if len(set(files)) != len(files):
            errors.append(f"{q.id}: a file appears more than once in primary/supporting")
        if not q.evidence.strip():
            errors.append(f"{q.id}: evidence is empty")
        for sym in q.relevant_symbols:
            if sym.file not in files:
                errors.append(
                    f"{q.id}: symbol {sym.name} is in {sym.file}, "
                    "which is not a primary/supporting file"
                )
        expected = expected_difficulty(q.category, len(files))
        if q.difficulty != expected:
            errors.append(
                f"{q.id}: difficulty is {q.difficulty!r} but the rubric gives {expected!r} "
                f"for a {q.category} question with {len(files)} file(s)"
            )

    if ds.frozen:
        actual = compute_questions_sha256(ds.questions)
        if ds.questions_sha256 != actual:
            errors.append(
                "dataset is frozen but its questions no longer match the recorded fingerprint "
                f"(recorded {ds.questions_sha256}, actual {actual}). Frozen datasets are never "
                "edited to suit retrieval results; a factual correction needs a new dataset "
                "version (see evaluation/README.md)."
            )
    return errors


def get_head_commit(repo_root: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return out.stdout.strip()


def check_against_repo(ds: EvalDataset, repo_root: Path) -> list[str]:
    """Every listed file must exist and every listed symbol must be found by the parser."""
    errors: list[str] = []
    symbols_by_file: dict[str, set[str]] = {}

    def symbols_of(path: str) -> set[str]:
        if path not in symbols_by_file:
            parsed = parse_python_file(repo_root, path)
            symbols_by_file[path] = {s.qualified_name for s in parsed.symbols}
        return symbols_by_file[path]

    for q in ds.questions:
        for path in q.relevant_files:
            if not (repo_root / path).is_file():
                errors.append(f"{q.id}: file does not exist: {path}")
        for sym in q.relevant_symbols:
            if not (repo_root / sym.file).is_file():
                continue  # already reported above
            if sym.name not in symbols_of(sym.file):
                errors.append(f"{q.id}: symbol {sym.name!r} not found in {sym.file}")
    return errors


def format_summary(ds: EvalDataset) -> str:
    by_category = Counter(q.category for q in ds.questions)
    by_difficulty = Counter(q.difficulty for q in ds.questions)
    primary = [len(q.primary) for q in ds.questions]
    supporting = sum(1 for q in ds.questions if q.supporting)
    state = f"FROZEN on {ds.frozen_on}" if ds.frozen else "not frozen"
    lines = [
        f"{ds.name}: {len(ds.questions)} questions on {ds.repository} @ {ds.commit[:10]} ({state})",
        "  category:   " + ", ".join(f"{c} {n}" for c, n in sorted(by_category.items())),
        "  difficulty: " + ", ".join(f"{d} {by_difficulty[d]}" for d in ("easy", "medium", "hard")),
        f"  primary files per question: min {min(primary)}, max {max(primary)}, "
        f"mean {sum(primary) / len(primary):.1f}; {supporting} questions have supporting files",
        f"  with relevant symbols: {sum(1 for q in ds.questions if q.relevant_symbols)}",
    ]
    return "\n".join(lines)


def freeze_dataset(path: Path, today: str) -> EvalDataset:
    """Record the fingerprint of the questions and mark the dataset frozen."""
    ds = load_dataset(path)
    ds.frozen = True
    ds.frozen_on = today
    ds.questions_sha256 = compute_questions_sha256(ds.questions)
    path.write_text(ds.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return ds


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.evaluation.validate", description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--repo-path", type=Path, help="checkout to validate against")
    parser.add_argument(
        "--freeze",
        metavar="YYYY-MM-DD",
        help="mark the dataset frozen as of this date and record the questions' fingerprint",
    )
    args = parser.parse_args(argv)

    if args.freeze:
        freeze_dataset(args.dataset, args.freeze)
    ds = load_dataset(args.dataset)
    print(format_summary(ds))
    errors = check_structure(ds)

    owner, name = ds.repository.split("/")
    repo_root: Path = args.repo_path or get_settings().repos_dir / owner / name
    if not repo_root.is_dir():
        errors.append(f"no checkout at {repo_root}; run python -m app.ingestion first")
    else:
        head = get_head_commit(repo_root)
        if head != ds.commit:
            errors.append(
                f"checkout is at {head}, dataset is pinned to {ds.commit}. To fix:\n"
                f"    git -C {repo_root} fetch --depth 1 origin {ds.commit}\n"
                f"    git -C {repo_root} checkout {ds.commit}"
            )
        else:
            errors += check_against_repo(ds, repo_root)

    if errors:
        print(f"\n{len(errors)} problem(s):", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print("\nOK: structure is valid and every file and symbol exists at the pinned commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
