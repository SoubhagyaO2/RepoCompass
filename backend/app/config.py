"""Application settings.

All configuration is read from environment variables (or a `.env` file) prefixed with
`REPOCOMPASS_`. Nothing secret is ever hard-coded here; see `.env.example`.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="REPOCOMPASS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "RepoCompass"
    # Where cloned repositories and index artifacts are stored.
    data_dir: Path = Path("data")

    # Embeddings. bge models expect this prefix on *queries* (not on documents); set it to
    # an empty string if you switch to a model that does not use one.
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_query_prefix: str = "Represent this sentence for searching relevant passages: "

    # Naive baseline chunking (Milestone 4).
    baseline_chunk_size: int = 1000
    baseline_chunk_overlap: int = 200

    # The answer layer (Milestone 13): a configurable provider; the key lives in backend/.env only.
    llm_provider: str = "gemini"
    gemini_model: str = "gemini-3.7-flash"
    gemini_api_key: SecretStr | None = Field(default=None, validation_alias="GEMINI_API_KEY")

    # The live index served by the Evidence API (Milestones 11-12).
    live_repository: str | None = None
    live_root: Path | None = None

    def live_state_dir(self, repository: str) -> Path:
        return self.data_dir / "live_state" / repository

    @property
    def live_qdrant_path(self) -> Path:
        return self.data_dir / "qdrant_live"

    @property
    def qdrant_path(self) -> Path:
        return self.data_dir / "qdrant"

    @property
    def repos_dir(self) -> Path:
        return self.data_dir / "repos"

    @property
    def index_dir(self) -> Path:
        return self.data_dir / "index"


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (read the environment once per process)."""
    return Settings()
