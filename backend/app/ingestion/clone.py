"""Clone repositories with the git CLI."""

import os
import shutil
import stat
import subprocess
from pathlib import Path


class CloneError(RuntimeError):
    """Raised when a repository cannot be cloned."""


def clone_repository(clone_url: str, dest: Path, timeout_s: float = 600) -> Path:
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

    cmd = ["git", "clone", "--depth", "1", "--", clone_url, str(tmp)]
    # Fail instead of hanging on a credentials prompt (private or missing repos).
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
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
        raise CloneError(f"git clone failed: {e.stderr.strip()}") from e

    tmp.rename(dest)
    return dest


def _remove_tree(path: Path) -> None:
    """Delete a directory tree, including git's read-only object files on Windows."""

    def make_writable_and_retry(func, p, _exc) -> None:  # type: ignore[no-untyped-def]
        os.chmod(p, stat.S_IWRITE)
        func(p)

    if path.exists():
        shutil.rmtree(path, onexc=make_writable_and_retry)
