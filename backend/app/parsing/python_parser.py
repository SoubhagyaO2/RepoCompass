"""Extract symbols and imports from Python source using Tree-sitter.

Tree-sitter positions are byte offsets into the source, so we always work on the
raw bytes of a file (never a decoded/newline-normalised string). Slicing those bytes
is what lets every Symbol carry its original source text exactly.
"""

from pathlib import Path
from typing import Literal

import tree_sitter_python
from tree_sitter import Language, Node, Parser

from app.models.symbols import Import, ParsedFile, Symbol, SymbolKind

PY_LANGUAGE = Language(tree_sitter_python.language())
_parser = Parser(PY_LANGUAGE)

# Statements whose bodies we look inside for definitions (e.g. `if TYPE_CHECKING:`).
# Function bodies are deliberately NOT in this set: nested functions stay part of
# their parent's source rather than becoming symbols of their own.
_CONTAINER_TYPES = {
    "module",
    "block",
    "if_statement",
    "elif_clause",
    "else_clause",
    "try_statement",
    "except_clause",
    "finally_clause",
    "with_statement",
    "for_statement",
    "while_statement",
}


def parse_python_source(source: bytes, path: str) -> ParsedFile:
    """Parse Python source bytes. Never raises on syntax errors; see `has_errors`."""
    tree = _parser.parse(source)
    root = tree.root_node
    return ParsedFile(
        path=path,
        language="Python",
        symbols=_collect_symbols(root, source, path),
        imports=_collect_imports(root),
        has_errors=root.has_error,
    )


def parse_python_file(repo_root: Path, rel_path: str) -> ParsedFile:
    return parse_python_source((repo_root / rel_path).read_bytes(), rel_path)


# --- symbols -------------------------------------------------------------------------


def _collect_symbols(root: Node, source: bytes, path: str) -> list[Symbol]:
    symbols: list[Symbol] = []

    def visit(node: Node, class_scope: list[str]) -> None:
        for child in node.named_children:
            # `outer` spans decorators too; `definition` is the def/class itself.
            outer, definition = child, child
            if child.type == "decorated_definition":
                definition = child.child_by_field_name("definition") or child

            if definition.type == "function_definition":
                symbols.append(
                    _make_symbol(outer, definition, "function", class_scope, source, path)
                )
            elif definition.type == "class_definition":
                symbol = _make_symbol(outer, definition, "class", class_scope, source, path)
                symbols.append(symbol)
                body = definition.child_by_field_name("body")
                if body is not None:
                    visit(body, [*class_scope, symbol.name])
            elif child.type in _CONTAINER_TYPES:
                visit(child, class_scope)

    visit(root, [])
    return symbols


def _make_symbol(
    outer: Node,
    definition: Node,
    kind: Literal["function", "class"],
    class_scope: list[str],
    source: bytes,
    path: str,
) -> Symbol:
    name_node = definition.child_by_field_name("name")
    name = _text(name_node) if name_node else "<anonymous>"
    symbol_kind: SymbolKind = "method" if kind == "function" and class_scope else kind
    parent = ".".join(class_scope) or None
    return Symbol(
        name=name,
        qualified_name=f"{parent}.{name}" if parent else name,
        kind=symbol_kind,
        file=path,
        start_line=outer.start_point.row + 1,
        end_line=outer.end_point.row + 1,
        content=source[outer.start_byte : outer.end_byte].decode("utf-8", errors="replace"),
        parent=parent,
    )


# --- imports -------------------------------------------------------------------------


def _collect_imports(root: Node) -> list[Import]:
    """Collect every import in the file, including ones inside functions or `if` blocks."""
    imports: list[Import] = []
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == "import_statement":
            imports.extend(_plain_import(node))
        elif node.type in ("import_from_statement", "future_import_statement"):
            imports.extend(_from_import(node))
        else:
            stack.extend(reversed(node.named_children))
    return imports


def _plain_import(node: Node) -> list[Import]:
    """`import a.b as c, d`"""
    line = node.start_point.row + 1
    result = []
    for name_node in node.children_by_field_name("name"):
        module, alias = _name_and_alias(name_node)
        result.append(Import(module=module, alias=alias, line=line))
    return result


def _from_import(node: Node) -> list[Import]:
    """`from ..pkg import a as b, c` / `from m import *` / `from __future__ import x`"""
    line = node.start_point.row + 1
    module, level = "__future__", 0
    module_node = node.child_by_field_name("module_name")
    if module_node is not None:
        if module_node.type == "relative_import":
            module, level = "", 0
            for part in module_node.named_children:
                if part.type == "import_prefix":
                    level = len(_text(part))
                elif part.type == "dotted_name":
                    module = _text(part)
        else:
            module = _text(module_node)

    if any(c.type == "wildcard_import" for c in node.named_children):
        return [Import(module=module, name="*", level=level, line=line)]

    result = []
    for name_node in node.children_by_field_name("name"):
        name, alias = _name_and_alias(name_node)
        result.append(Import(module=module, name=name, alias=alias, level=level, line=line))
    return result


def _name_and_alias(node: Node) -> tuple[str, str | None]:
    if node.type == "aliased_import":
        name_node = node.child_by_field_name("name")
        alias_node = node.child_by_field_name("alias")
        return (_text(name_node) if name_node else "", _text(alias_node) if alias_node else None)
    return _text(node), None


def _text(node: Node) -> str:
    return (node.text or b"").decode("utf-8", errors="replace")
