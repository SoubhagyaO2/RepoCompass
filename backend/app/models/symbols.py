"""Data models produced by parsing: symbols (functions, methods, classes) and imports."""

from typing import Literal

from pydantic import BaseModel

SymbolKind = Literal["function", "method", "class"]


class Symbol(BaseModel):
    """A function, method or class definition with its exact location and source."""

    name: str  # "get_user"
    # "UserService.get_user". NOT unique: @overload stubs and conditional definitions
    # repeat a name, so (file, start_line) is what identifies a symbol.
    qualified_name: str
    kind: SymbolKind
    file: str  # repo-relative POSIX path
    start_line: int  # 1-based, inclusive; includes decorators
    end_line: int  # 1-based, inclusive
    content: str  # original source text, byte-for-byte
    parent: str | None = None  # qualified name of the enclosing class, if any
    bases: list[str] = []  # classes only: the base-class expressions as written


class Call(BaseModel):
    """One call expression (or decorator application), attributed to its enclosing symbol."""

    caller: str | None  # qualified name of the enclosing symbol; None = module-level code
    name: str  # the name being called: "ensure_sync" for self.ensure_sync(...)
    receiver: str | None  # the object it is called on as written ("self", "app"); None if plain
    line: int  # 1-based
    kind: Literal["call", "decorator"] = "call"


class Import(BaseModel):
    """One imported binding. `from a.b import c as d` -> module="a.b", name="c", alias="d"."""

    module: str  # "" for `from . import x`
    name: str | None = None  # None for plain `import module`; "*" for wildcard imports
    alias: str | None = None
    level: int = 0  # number of leading dots in a relative import
    line: int  # 1-based


class ParsedFile(BaseModel):
    path: str
    language: str
    symbols: list[Symbol]
    imports: list[Import]
    has_errors: bool  # True if the parser had to recover from syntax errors
    calls: list[Call] = []
