"""Command line for inspecting parser output.

python -m app.parsing <file.py>        list the symbols and imports in one file
python -m app.parsing <repo-dir>       parse every Python file and print totals
"""

import argparse
import sys
import time
from collections import Counter
from pathlib import Path

from app.ingestion.discovery import discover_files
from app.models.symbols import ParsedFile
from app.parsing.python_parser import parse_python_file


def print_file(parsed: ParsedFile) -> None:
    print(f"{parsed.path}  (syntax errors: {parsed.has_errors})\n")
    print("Symbols:")
    for s in parsed.symbols:
        print(f"  {s.kind:<8} {s.qualified_name:<40} lines {s.start_line}-{s.end_line}")
    print("\nImports:")
    for imp in parsed.imports:
        module = "." * imp.level + imp.module
        target = f"from {module} import {imp.name}" if imp.name else f"import {module}"
        alias = f" as {imp.alias}" if imp.alias else ""
        print(f"  line {imp.line:<5} {target}{alias}")


def print_repo(root: Path) -> None:
    py_files = [f.path for f in discover_files(root) if f.language == "Python"]
    start = time.perf_counter()
    parsed = [parse_python_file(root, path) for path in py_files]
    elapsed = time.perf_counter() - start

    kinds = Counter(s.kind for p in parsed for s in p.symbols)
    with_errors = [p.path for p in parsed if p.has_errors]
    print(f"Parsed {len(parsed):,} Python files in {elapsed:.2f}s\n")
    for kind, label in (("class", "classes"), ("function", "functions"), ("method", "methods")):
        print(f"  {label + ':':<11} {kinds[kind]:>6,}")
    print(f"  {'imports:':<11} {sum(len(p.imports) for p in parsed):>6,}")
    print(f"\nFiles with syntax errors: {len(with_errors)}")
    for path in with_errors[:10]:
        print(f"  {path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.parsing", description=__doc__)
    parser.add_argument("path", type=Path, help="a .py file or a cloned repository directory")
    args = parser.parse_args(argv)
    path: Path = args.path

    if path.is_file():
        print_file(parse_python_file(path.parent, path.name))
    elif path.is_dir():
        print_repo(path)
    else:
        print(f"error: {path} does not exist", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
