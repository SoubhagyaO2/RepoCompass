"""Validate GitHub repository URLs."""

import re
from urllib.parse import urlparse

from app.models.repository import RepoRef

_GITHUB_HOSTS = {"github.com", "www.github.com"}
# GitHub rules: owners are alphanumeric + single hyphens (max 39 chars, no leading hyphen);
# repository names use letters, digits, '.', '-', '_'.
_OWNER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}$")
_REPO_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


class InvalidRepoURLError(ValueError):
    """Raised when a string is not a supported GitHub repository URL."""


def parse_github_url(url: str) -> RepoRef:
    """Parse `https://github.com/<owner>/<repo>` (optionally ending in `.git` or `/`).

    Query strings and fragments are ignored. Anything deeper than owner/repo
    (e.g. `/tree/main/src`) is rejected so the caller always gets a whole repository.
    """
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("https", "http"):
        raise InvalidRepoURLError(f"URL must start with https:// — got {url!r}")
    if (parsed.hostname or "").lower() not in _GITHUB_HOSTS:
        raise InvalidRepoURLError(f"Only github.com repositories are supported — got {url!r}")

    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) != 2:
        raise InvalidRepoURLError(f"Expected https://github.com/<owner>/<repo> — got {url!r}")

    owner, name = parts[0], parts[1].removesuffix(".git")
    if not _OWNER_RE.match(owner):
        raise InvalidRepoURLError(f"Invalid GitHub owner {owner!r}")
    if not _REPO_RE.match(name) or name in {".", ".."}:
        raise InvalidRepoURLError(f"Invalid repository name {name!r}")
    return RepoRef(owner=owner, name=name)
