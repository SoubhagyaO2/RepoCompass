"""Command line: python -m app.indexing <github-url> [--show N]"""

import argparse
import sys

from app.indexing.service import LARGE_CHUNK_CHARS, IndexResult, index_repository
from app.ingestion.clone import CloneError
from app.ingestion.url import InvalidRepoURLError
from app.models.chunk import Chunk

PREVIEW_LINES = 12


def format_stats(result: IndexResult) -> str:
    s = result.stats
    t = s.chunks_by_type
    lines = [
        f"Repository: {s.repository}",
        "",
        f"Indexed {s.files_indexed:,} files",
        f"Functions: {t.get('function', 0):,}",
        f"Methods: {t.get('method', 0):,}",
        f"Classes: {t.get('class', 0):,}",
        f"Module-level chunks: {t.get('module', 0):,}",
        f"Total chunks: {len(result.chunks):,}",
        "",
        "By file category: "
        + ", ".join(f"{k} {v:,}" for k, v in sorted(s.chunks_by_category.items())),
        f"Chunk size (chars): median {s.chunk_chars.median:,}, p95 {s.chunk_chars.p95:,}, "
        f"max {s.chunk_chars.max:,}",
        f"Chunks over {LARGE_CHUNK_CHARS:,} chars: {s.large_chunks:,}",
        f"Files with syntax errors: {s.files_with_syntax_errors}",
        "",
        f"Saved to {result.chunks_path}",
        f"Took {s.seconds:.2f}s (parse + chunk)",
    ]
    return "\n".join(lines)


def format_chunk(chunk: Chunk) -> str:
    header = (
        f"[{chunk.symbol_type}] {chunk.qualified_name}  "
        f"{chunk.file_path}:{chunk.start_line}-{chunk.end_line}  ({chunk.category})"
    )
    body = chunk.content.splitlines()
    preview = body[:PREVIEW_LINES]
    if len(body) > PREVIEW_LINES:
        preview.append(f"    ... ({len(body) - PREVIEW_LINES} more lines)")
    return header + "\n" + "\n".join("    " + line for line in preview)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.indexing", description=__doc__)
    parser.add_argument("url", help="e.g. https://github.com/pallets/flask")
    parser.add_argument("--show", type=int, default=0, metavar="N", help="print N sample chunks")
    args = parser.parse_args(argv)

    try:
        result = index_repository(args.url)
    except (InvalidRepoURLError, CloneError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(format_stats(result))
    if args.show > 0 and result.chunks:
        step = max(1, len(result.chunks) // args.show)
        print("\n--- sample chunks ---")
        for chunk in result.chunks[::step][: args.show]:
            print("\n" + format_chunk(chunk))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
