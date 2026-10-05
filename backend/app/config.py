"""Application settings.

All configuration is read from environment variables (or a `.env` file) prefixed with
`REPOCOMPASS_`. Nothing secret is ever hard-coded here; see `.env.example`.
"""

from functools import lru_cache
from pathlib import Path

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

    @property
    def repos_dir(self) -> Path:
        return self.data_dir / "repos"


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (read the environment once per process)."""
    return Settings()
