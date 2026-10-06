"""The indexing unit. Every retrieval strategy and the code graph build on this model."""

from typing import Literal

from pydantic import BaseModel

ChunkType = Literal["function", "method", "class", "module"]
FileCategory = Literal["source", "test", "docs"]


class Chunk(BaseModel):
    """A contiguous slice of one file, aligned to a logical code unit.

    `content` is always the original source text of lines `start_line..end_line`
    (1-based, inclusive), so a chunk can be cited exactly.
    """

    id: str  # "<repository>:<file_path>:<start_line>" — chunks in a file never overlap
    repository: str  # "pallets/flask"
    file_path: str  # repo-relative POSIX path
    language: str
    symbol_name: str  # "get_user"; "<module>" for module-level code
    qualified_name: str  # "UserService.get_user"
    symbol_type: ChunkType
    start_line: int
    end_line: int
    content: str
    imports: list[str]  # the file's imports, e.g. "from .scaffold import Scaffold"
    category: FileCategory
    parent: str | None = None  # qualified name of the enclosing class
