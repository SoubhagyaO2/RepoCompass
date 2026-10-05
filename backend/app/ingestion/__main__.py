"""Command line: python -m app.ingestion <github-url>"""

import argparse
import sys
import time

from app.ingestion.clone import CloneError
from app.ingestion.service import ingest_repository
from app.ingestion.url import InvalidRepoURLError
from app.models.repository import RepositorySummary


def format_summary(summary: RepositorySummary) -> str:
    width = max((len(lang) for lang in summary.files_by_language), default=0)
    lines = [f"Repository: {summary.repository}", "", f"Files: {summary.total_files:,}"]
    lines += [
        f"  {lang + ':':<{width + 1}} {count:>7,}"
        for lang, count in summary.files_by_language.items()
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.ingestion",
        description="Clone a GitHub repository and summarise its files by language.",
    )
    parser.add_argument("url", help="e.g. https://github.com/pallets/flask")
    args = parser.parse_args(argv)

    start = time.perf_counter()
    try:
        repo = ingest_repository(args.url)
    except (InvalidRepoURLError, CloneError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(format_summary(repo.summary()))
    print(f"\nLocal path: {repo.local_path}")
    print(f"Took {time.perf_counter() - start:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
