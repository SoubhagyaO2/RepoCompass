"""AST-aware chunking of Python files.

Chunks follow the logical structure found by the parser instead of character counts:

* each function / method  -> one chunk (decorators included)
* each class              -> one chunk for its *header* (signature, docstring, class-level
                             attributes up to the first method); its methods are separate
                             chunks, so no source text is indexed twice
* module-level code       -> "module" chunks for the code outside any function or class
                             (constants, `app = Flask(...)`, `__init__.py` re-exports, ...)

Known limitation: code that sits *between* methods inside a class body (e.g. an attribute
assigned after the first method) belongs to no chunk.
"""

from app.chunking.categories import classify_file
from app.models.chunk import Chunk, ChunkType
from app.models.symbols import Import, ParsedFile, Symbol
from app.parsing.python_parser import parse_python_source

MODULE_NAME = "<module>"


def format_import(imp: Import) -> str:
    alias = f" as {imp.alias}" if imp.alias else ""
    if imp.name is None:
        return f"import {imp.module}{alias}"
    return f"from {'.' * imp.level}{imp.module} import {imp.name}{alias}"


def chunk_python_source(
    source: bytes, path: str, repository: str
) -> tuple[ParsedFile, list[Chunk]]:
    """Parse and chunk one file. Returns the parse result too (callers want `has_errors`)."""
    parsed = parse_python_source(source, path)
    text = source.decode("utf-8", errors="replace")
    return parsed, chunk_parsed_file(parsed, text, repository)


def chunk_parsed_file(parsed: ParsedFile, source: str, repository: str) -> list[Chunk]:
    """Chunk an already-parsed file. `source` must be the text the file was parsed from."""
    lines = _split_lines(source)
    imports = [format_import(i) for i in parsed.imports]
    category = classify_file(parsed.path)

    def make(
        name: str,
        qualified_name: str,
        kind: ChunkType,
        start: int,
        end: int,
        content: str,
        parent: str | None = None,
    ) -> Chunk:
        return Chunk(
            id=f"{repository}:{parsed.path}:{start}",
            repository=repository,
            file_path=parsed.path,
            language=parsed.language,
            symbol_name=name,
            qualified_name=qualified_name,
            symbol_type=kind,
            start_line=start,
            end_line=end,
            content=content,
            imports=imports,
            category=category,
            parent=parent,
        )

    chunks: list[Chunk] = []
    for sym in parsed.symbols:
        end = sym.end_line
        if sym.kind == "class":
            end = _class_header_end(sym, parsed.symbols, lines)
        # Whole lines (not Symbol.content, which starts at the `def` keyword and drops the
        # first line's indentation), so every chunk is exactly lines[start_line..end_line].
        content = _join(lines, sym.start_line, end)
        chunks.append(
            make(sym.name, sym.qualified_name, sym.kind, sym.start_line, end, content, sym.parent)
        )

    for start, end in _module_ranges(parsed.symbols, lines):
        chunks.append(
            make(MODULE_NAME, MODULE_NAME, "module", start, end, _join(lines, start, end))
        )

    chunks.sort(key=lambda c: c.start_line)
    return chunks


def _class_header_end(cls: Symbol, symbols: list[Symbol], lines: list[str]) -> int:
    """Last line of the class header: everything before its first direct method/inner class."""
    child_starts = [
        s.start_line
        for s in symbols
        if s.parent == cls.qualified_name
        and cls.start_line < s.start_line
        and s.end_line <= cls.end_line
    ]
    if not child_starts:
        return cls.end_line
    end = min(child_starts) - 1
    while end > cls.start_line and not lines[end - 1].strip():
        end -= 1
    return end


def _module_ranges(symbols: list[Symbol], lines: list[str]) -> list[tuple[int, int]]:
    """Line ranges outside every top-level symbol, minus blank edges and blank-only gaps."""
    ranges: list[tuple[int, int]] = []
    cursor = 1
    for start, end in sorted((s.start_line, s.end_line) for s in symbols if s.parent is None):
        if start > cursor:
            ranges.append((cursor, start - 1))
        cursor = max(cursor, end + 1)
    if cursor <= len(lines):
        ranges.append((cursor, len(lines)))

    trimmed: list[tuple[int, int]] = []
    for start, end in ranges:
        while start <= end and not lines[start - 1].strip():
            start += 1
        while end >= start and not lines[end - 1].strip():
            end -= 1
        if start <= end:
            trimmed.append((start, end))
    return trimmed


def _split_lines(source: str) -> list[str]:
    # Split on "\n" only: that is how Tree-sitter counts rows. str.splitlines() would also
    # split on form feeds and Unicode separators and make line numbers disagree.
    lines = source.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _join(lines: list[str], start: int, end: int) -> str:
    # With CRLF files each line keeps its "\r"; drop the last one so the text has no
    # trailing line terminator.
    return "\n".join(lines[start - 1 : end]).removesuffix("\r")
