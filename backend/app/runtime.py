"""What the process can tell about its own memory (Linux containers; unknown elsewhere)."""

from pathlib import Path

CGROUP_LIMIT_FILES = (
    Path("/sys/fs/cgroup/memory.max"),  # cgroup v2
    Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"),  # cgroup v1
)
_NO_LIMIT_BYTES = 2**60  # cgroup v1 reports a number near 2**63 when there is no limit


def memory_limit_mb(paths: tuple[Path, ...] = CGROUP_LIMIT_FILES) -> int | None:
    """The container's memory limit in MiB, or None when there is none or it cannot be read."""
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not text.isdigit():
            return None  # "max": no limit
        value = int(text)
        return None if value >= _NO_LIMIT_BYTES else value // (1024 * 1024)
    return None


def rss_mb(status_path: Path = Path("/proc/self/status")) -> float | None:
    """Resident memory of this process in MiB (None where /proc is unavailable)."""
    try:
        for line in status_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
    except (OSError, ValueError, IndexError):
        pass
    return None
