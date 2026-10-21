"""What the server can tell about its own memory, so a host that is too small fails with a clear
message instead of being killed in the middle of indexing."""

from pathlib import Path

import pytest

from app.runtime import memory_limit_mb, rss_mb


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_a_cgroup_v2_limit_is_read_in_mib(tmp_path: Path) -> None:
    v2 = write(tmp_path / "memory.max", str(512 * 1024 * 1024) + "\n")
    assert memory_limit_mb(paths=(v2,)) == 512


def test_no_limit_means_unknown(tmp_path: Path) -> None:
    assert memory_limit_mb(paths=(write(tmp_path / "memory.max", "max\n"),)) is None
    huge = write(tmp_path / "limit_in_bytes", str(2**62))
    assert memory_limit_mb(paths=(huge,)) is None  # cgroup v1 reports a huge number for "none"
    assert memory_limit_mb(paths=(tmp_path / "missing",)) is None


def test_the_first_readable_file_wins(tmp_path: Path) -> None:
    v1 = write(tmp_path / "v1", str(1024 * 1024 * 1024))
    assert memory_limit_mb(paths=(tmp_path / "missing", v1)) == 1024


def test_resident_memory_is_read_from_proc_status(tmp_path: Path) -> None:
    status = write(tmp_path / "status", "Name:\tpython\nVmRSS:\t  204800 kB\nThreads:\t4\n")
    assert rss_mb(status_path=status) == pytest.approx(200.0)
    assert rss_mb(status_path=tmp_path / "nope") is None
