"""The result type shared by every retrieval strategy, so they can be compared directly."""

from pydantic import BaseModel

from app.models.chunk import FileCategory


class SearchHit(BaseModel):
    file_path: str
    start_line: int
    end_line: int
    score: float  # meaning depends on the retriever (cosine similarity, BM25, ...)
    content: str
    category: FileCategory
    # Only set by retrievers that know about code structure (AST chunks, from Milestone 7 on).
    symbol_name: str | None = None
    symbol_type: str | None = None
