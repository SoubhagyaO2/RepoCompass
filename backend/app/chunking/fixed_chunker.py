"""Naive fixed-size chunking: the baseline we want the AST-aware chunker to beat.

The text is cut into windows of `size` characters, each starting `size - overlap`
characters after the previous one. Nothing about the code is considered, so windows
routinely start and end in the middle of a statement or function.
"""

from bisect import bisect_left

from app.chunking.categories import classify_file
from app.models.chunk import TextChunk

DEFAULT_SIZE = 1000
DEFAULT_OVERLAP = 200


def chunk_fixed(
    text: str,
    path: str,
    repository: str,
    size: int = DEFAULT_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> list[TextChunk]:
    """Split `text` into overlapping character windows. Whitespace-only windows are dropped."""
    if size <= 0 or not 0 <= overlap < size:
        raise ValueError(f"need size > 0 and 0 <= overlap < size (got {size=}, {overlap=})")

    newlines = _newline_offsets(text)
    category = classify_file(path)
    step = size - overlap
    chunks: list[TextChunk] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        content = text[start:end]
        if content.strip():
            chunks.append(
                TextChunk(
                    id=f"{repository}:{path}:{start}",
                    repository=repository,
                    file_path=path,
                    # Number of newlines before an offset + 1 is the 1-based line of that offset.
                    start_line=bisect_left(newlines, start) + 1,
                    end_line=bisect_left(newlines, end - 1) + 1,
                    content=content,
                    category=category,
                )
            )
        if end == len(text):
            break
        start += step
    return chunks


def _newline_offsets(text: str) -> list[int]:
    offsets: list[int] = []
    i = text.find("\n")
    while i != -1:
        offsets.append(i)
        i = text.find("\n", i + 1)
    return offsets
