"""Command line for the live index and the Evidence API.

    python -m app.evidence --repository flask --root path/to/flask --build
    python -m app.evidence --repository flask --root path/to/flask "where is the session saved"

`--build` builds the index if none exists, otherwise updates it incrementally (Milestone 11).
The index lives under REPOCOMPASS_DATA_DIR; only one process can open it at a time.
"""

import argparse
import sys
from pathlib import Path

from app.config import get_settings
from app.evidence.builder import EvidenceBuilder, EvidenceQueryError
from app.evidence.service import open_live_index
from app.indexing.live_index import IndexStateError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.evidence", description=__doc__)
    parser.add_argument("query", nargs="?", help="the question to retrieve evidence for")
    parser.add_argument("--repository", required=True, help="name of the live index")
    parser.add_argument("--root", required=True, type=Path, help="the repository's working tree")
    parser.add_argument("--build", action="store_true", help="build or incrementally update")
    parser.add_argument("--revision", help="commit recorded in the response (informational)")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--text", action="store_true", help="include the chunk text")
    args = parser.parse_args(argv)
    if not args.build and not args.query:
        parser.error("give a query, or --build")

    index, store = open_live_index(get_settings(), args.repository, args.root)
    try:
        try:
            exists = index.load()
        except IndexStateError as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        if args.build:
            stats = index.update() if exists else index.build_full()
            print(
                f"{'updated' if exists else 'built'}: +{stats.added} ~{stats.modified} "
                f"-{stats.deleted} ={stats.unchanged} files in {stats.seconds['total']:.1f}s",
                file=sys.stderr,
            )
        elif not exists:
            print("error: no index; run with --build first", file=sys.stderr)
            return 2
        if args.query:
            try:
                response = EvidenceBuilder(index, args.revision).build(
                    args.query, args.top_k, args.text
                )
            except EvidenceQueryError as error:
                print(f"error: {error}", file=sys.stderr)
                return 2
            print(response.model_dump_json(indent=1))
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
