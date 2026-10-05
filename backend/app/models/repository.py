"""Data models describing a repository and its files."""

from collections import Counter
from pathlib import Path

from pydantic import BaseModel, ConfigDict

OTHER_LANGUAGE = "Other"


class RepoRef(BaseModel):
    """A validated reference to a GitHub repository (owner + name)."""

    model_config = ConfigDict(frozen=True)

    owner: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def clone_url(self) -> str:
        # Rebuilt from validated parts rather than echoing user input to git.
        return f"https://github.com/{self.owner}/{self.name}.git"


class SourceFile(BaseModel):
    """One file inside a repository, identified by its path relative to the repo root."""

    path: str  # POSIX-style relative path, e.g. "fastapi/routing.py"
    language: str | None  # None when the extension is not a known programming language
    size_bytes: int


class RepositorySummary(BaseModel):
    repository: str
    total_files: int
    files_by_language: dict[str, int]  # most common first, "Other" last


class IngestedRepository(BaseModel):
    """Result of ingestion: where the clone lives and which files it contains."""

    ref: RepoRef
    local_path: Path
    files: list[SourceFile]

    def summary(self) -> RepositorySummary:
        counts = Counter(f.language or OTHER_LANGUAGE for f in self.files)
        other = counts.pop(OTHER_LANGUAGE, 0)
        by_language = dict(counts.most_common())
        if other:
            by_language[OTHER_LANGUAGE] = other
        return RepositorySummary(
            repository=self.ref.full_name,
            total_files=len(self.files),
            files_by_language=by_language,
        )
