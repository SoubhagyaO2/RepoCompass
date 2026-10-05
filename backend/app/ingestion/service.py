"""Ingestion entry point used by the CLI (and later by the API and MCP server)."""

from app.config import Settings, get_settings
from app.ingestion.clone import clone_repository
from app.ingestion.discovery import discover_files
from app.ingestion.url import parse_github_url
from app.models.repository import IngestedRepository


def ingest_repository(url: str, settings: Settings | None = None) -> IngestedRepository:
    """Validate `url`, clone it under the data directory, and list its files."""
    settings = settings or get_settings()
    ref = parse_github_url(url)
    dest = settings.repos_dir / ref.owner / ref.name
    clone_repository(ref.clone_url, dest)
    return IngestedRepository(ref=ref, local_path=dest, files=discover_files(dest))
