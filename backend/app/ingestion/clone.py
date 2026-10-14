"""Clone repositories with the git CLI."""

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path


class CloneError(RuntimeError):
    """Raised when a repository cannot be cloned."""


# The only environment variables git sees when cloning for a web request: nothing secret.
_GIT_ENV_KEYS = ("PATH", "SYSTEMROOT", "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "LANG")

# git options for a clone requested over HTTP: https only, no redirects, no hooks, no symlinks.
_HARDENED_OPTIONS = [
    "-c", "protocol.allow=never",
    "-c", "protocol.https.allow=always",
    "-c", "http.followRedirects=false",
    "-c", "core.symlinks=false",
    "-c", "core.hooksPath=",
    "-c", "credential.helper=",
]  # fmt: skip


ACCESS_MESSAGE = (
    "This repository could not be accessed without credentials. It is private or does not "
    "exist (GitHub answers the same way for both). RepoCompass indexes public repositories only: "
    "make the repository public, or check the owner and name in the URL."
)
_ACCESS_SIGNS = (
    "could not read username",
    "could not read password",
    "terminal prompts disabled",
    "authentication failed",
    "invalid username or password",
    "repository not found",
    "error: 401",
    "error: 403",
    "error: 404",
    "not found",
)
_NETWORK_SIGNS = (
    "could not resolve host",
    "failed to connect",
    "connection timed out",
    "connection refused",
    "ssl",
    "unable to access",
)


# Local file paths (Windows or POSIX) in git's text; URLs are left alone.
_PATH_RE = re.compile(r"(?<![:/\w])(?:[A-Za-z]:[\\/]|/)[^\s'\"]+")


def explain_clone_failure(stderr: str) -> str:
    """A message a user can act on, from git's stderr: never the raw text, which names local
    paths and (for private repositories) a misleading credentials prompt."""
    text = (stderr or "").lower()
    if "error: 429" in text or "rate limit" in text or "too many requests" in text:
        return "GitHub is rate-limiting requests from this server; try again later."
    if "returned error: 5" in text:
        return "GitHub is temporarily unavailable (a server error); try again later."
    if any(sign in text for sign in _ACCESS_SIGNS):
        return ACCESS_MESSAGE
    if any(sign in text for sign in _NETWORK_SIGNS):
        return "The server could not reach github.com (network or certificate problem)."
    summary = " ".join(_PATH_RE.sub("<path>", stderr or "").split())[:200]
    return f"git clone failed: {summary}"


def directory_size_mb(root: Path) -> float:
    """Size of the working tree in MiB (the .git directory is not counted)."""
    total = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for name in filenames:
            path = Path(dirpath) / name
            if not path.is_symlink():
                total += path.stat().st_size
    return total / 1_048_576


def clone_repository(
    clone_url: str, dest: Path, timeout_s: float = 600, hardened: bool = False
) -> Path:
    """Shallow-clone `clone_url` into `dest` and return `dest`.

    If `dest` already holds a clone it is reused as-is; refreshing an existing
    clone is the job of incremental indexing (Milestone 12).

    The clone is written to a temporary sibling directory and renamed into place
    only on success, so a failed or interrupted clone never looks like a valid one.
    """
    if (dest / ".git").is_dir():
        return dest
    if dest.exists():
        raise CloneError(f"{dest} exists but is not a git repository")

    tmp = dest.with_name(dest.name + ".partial")
    _remove_tree(tmp)  # leftover from an earlier interrupted run
    dest.parent.mkdir(parents=True, exist_ok=True)

    options = (
        [*_HARDENED_OPTIONS, "clone", "--depth", "1", "--no-tags"]
        if hardened
        else ["clone", "--depth", "1"]
    )
    cmd = ["git", *options, "--", clone_url, str(tmp)]
    # Fail instead of hanging on a credentials prompt (private or missing repos).
    base = (
        {k: os.environ[k] for k in _GIT_ENV_KEYS if k in os.environ}
        if hardened
        else dict(os.environ)
    )
    env = {**base, "GIT_TERMINAL_PROMPT": "0"}
    if hardened:
        # Ignore any system or per-user git configuration (credential helpers, URL rewrites).
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    try:
        subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            env=env,
        )
    except FileNotFoundError as e:
        raise CloneError("git is not installed or not on PATH") from e
    except subprocess.TimeoutExpired as e:
        _remove_tree(tmp)
        raise CloneError(f"git clone timed out after {timeout_s:.0f}s") from e
    except subprocess.CalledProcessError as e:
        _remove_tree(tmp)
        detail = (e.stderr or "").replace(str(tmp), "<clone>").replace(str(dest), "<clone>")
        raise CloneError(explain_clone_failure(detail)) from e

    tmp.rename(dest)
    return dest


def _remove_tree(path: Path) -> None:
    """Delete a directory tree, including git's read-only object files on Windows."""

    def make_writable_and_retry(func, p, _exc) -> None:  # type: ignore[no-untyped-def]
        os.chmod(p, stat.S_IWRITE)
        func(p)

    if path.exists():
        shutil.rmtree(path, onexc=make_writable_and_retry)
