"""Connecting a repository by URL: validation, isolation, limits and the indexing job. The clone
is replaced by a function that writes a small repository, so everything else is the real
pipeline (real chunking, a real local vector store, a stand-in embedder)."""

import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from answer_fixtures import FakeLLM, output

from app.config import Settings
from app.ingestion.clone import CloneError
from app.ingestion.url import InvalidRepoURLError
from app.repositories import (
    IndexingDisabled,
    QueueFull,
    RegistryFull,
    RepositoryNotFound,
    RepositoryNotReady,
    RepositoryRegistry,
    repository_id,
)
from app.retrieval.embedder import HashingEmbedder
from app.retrieval.vector_store import VectorStore

SOURCES = {
    "pkg/__init__.py": "",
    "pkg/signer.py": "class Signer:\n    def verify(self, value):\n        return value == 'ok'\n",
    "pkg/util.py": "def helper(x):\n    return x + 1\n",
}


def fake_clone(files: dict[str, str] | None = None, calls: list[str] | None = None) -> Any:
    def clone(url: str, dest: Path, timeout_s: float = 0, hardened: bool = False) -> Path:
        assert hardened, "API clones must be hardened"
        if calls is not None:
            calls.append(url)
        for rel, text in (files or SOURCES).items():
            path = dest / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        (dest / ".git").mkdir(exist_ok=True)
        return dest

    return clone


@pytest.fixture
def store(tmp_path: Path) -> Iterator[VectorStore]:
    with VectorStore(tmp_path / "qdrant") as s:
        yield s


def make(tmp_path: Path, store: VectorStore, clone: Any = None, llm: Any = None, **over: Any):
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", **over)
    registry = RepositoryRegistry(
        settings, store, HashingEmbedder(), llm=llm, clone=clone or fake_clone()
    )
    return registry, settings


# --- ids and URL validation ---


def test_repository_ids_are_stable_case_insensitive_and_unambiguous() -> None:
    assert (
        repository_id("Pallets", "Flask")
        == repository_id("pallets", "flask")
        == "gh__pallets__flask"
    )
    assert repository_id("a", "b__c") != repository_id("a-b", "c")


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/o/r",  # not https
        "ftp://github.com/o/r",
        "https://gitlab.com/o/r",
        "https://github.com.evil.example/o/r",
        "https://github.com@evil.example/o/r",
        "https://evil.example/github.com/o/r",
        "https://127.0.0.1/o/r",
        "https://localhost:8000/o/r",
        "https://169.254.169.254/latest/meta-data",
        "file:///etc/passwd",
        "https://github.com/o",
        "https://github.com/o/r/tree/main",
        "https://github.com/o/../r",
        "https://github.com/o/r/../../etc",
        "https://github.com:8443/o/r",
        "https://user:secret@github.com/o/r",
        "git@github.com:o/r.git",
        "-oProxyCommand=evil",
        "",
        "https://github.com/" + "a" * 200 + "/r",
    ],
)
def test_unsafe_or_unsupported_urls_are_rejected_before_any_network_use(
    tmp_path: Path, store: VectorStore, url: str
) -> None:
    calls: list[str] = []
    registry, _ = make(tmp_path, store, clone=fake_clone(calls=calls))
    with pytest.raises(InvalidRepoURLError):
        registry.submit(url)
    assert calls == [] and registry.list() == []


def test_the_clone_url_is_rebuilt_from_validated_parts(tmp_path: Path, store: VectorStore) -> None:
    calls: list[str] = []
    registry, _ = make(tmp_path, store, clone=fake_clone(calls=calls))
    entry = registry.submit("https://github.com/Pallets/Flask.git?x=1#frag")
    registry.wait(entry.id)
    assert calls == ["https://github.com/Pallets/Flask.git"]


# --- the job ---


def test_a_repository_is_cloned_indexed_and_becomes_ready(
    tmp_path: Path, store: VectorStore
) -> None:
    registry, settings = make(tmp_path, store)
    entry = registry.submit("https://github.com/acme/widgets")
    assert entry.id == "gh__acme__widgets" and entry.name == "acme/widgets"
    registry.wait(entry.id)
    done = registry.get(entry.id)
    assert done and done.status == "ready" and done.error is None
    assert done.files == 3 and done.chunks and done.chunks > 0
    builder = registry.builder(entry.id)
    evidence = builder.build("verify signer", 3, include_text=True)
    assert evidence.evidence and evidence.evidence[0].file.startswith("pkg/")
    assert (settings.live_state_dir(entry.id) / "manifest.json").exists()


def test_status_never_exposes_server_paths_or_secrets(tmp_path: Path, store: VectorStore) -> None:
    registry, settings = make(tmp_path, store)
    entry = registry.submit("https://github.com/acme/widgets")
    registry.wait(entry.id)
    public = str(registry.get(entry.id).public())
    assert str(settings.data_dir) not in public and "api_key" not in public.lower()
    assert set(registry.get(entry.id).public()) == {
        "id", "name", "status", "error", "files", "chunks", "files_total", "default",
    }  # fmt: skip


def test_asking_again_for_a_ready_repository_does_not_rebuild_it(
    tmp_path: Path, store: VectorStore
) -> None:
    calls: list[str] = []
    registry, _ = make(tmp_path, store, clone=fake_clone(calls=calls))
    first = registry.submit("https://github.com/acme/widgets")
    registry.wait(first.id)
    again = registry.submit("https://github.com/ACME/Widgets")
    assert again.id == first.id and again.status == "ready" and len(calls) == 1


def test_two_repositories_are_isolated(tmp_path: Path, store: VectorStore) -> None:
    other = {"lib/core.py": "def unique_other_function():\n    return 42\n"}
    calls: list[str] = []

    def clone(url: str, dest: Path, timeout_s: float = 0, hardened: bool = False) -> Path:
        files = other if "other" in url else SOURCES
        return fake_clone(files, calls)(url, dest, timeout_s, hardened)

    registry, settings = make(tmp_path, store, clone=clone)
    a = registry.submit("https://github.com/acme/widgets")
    registry.wait(a.id)
    b = registry.submit("https://github.com/acme/other")
    registry.wait(b.id)
    files_a = {i.file for i in registry.builder(a.id).build("signer verify", 8, False).evidence}
    files_b = {i.file for i in registry.builder(b.id).build("signer verify", 8, False).evidence}
    assert files_a and files_b and files_a.isdisjoint(files_b)
    assert all(f.startswith("pkg/") for f in files_a) and all(f.startswith("lib/") for f in files_b)
    assert settings.live_state_dir(a.id) != settings.live_state_dir(b.id)


def test_a_failed_clone_is_reported_without_paths_and_can_be_retried(
    tmp_path: Path, store: VectorStore
) -> None:
    state = {"fail": True}

    def clone(url: str, dest: Path, timeout_s: float = 0, hardened: bool = False) -> Path:
        if state["fail"]:
            raise CloneError(f"git clone failed: repository not found at {dest}")
        return fake_clone()(url, dest, timeout_s, hardened)

    registry, settings = make(tmp_path, store, clone=clone)
    entry = registry.submit("https://github.com/acme/private-repo")
    registry.wait(entry.id)
    failed = registry.get(entry.id)
    assert failed.status == "failed" and "not found" in failed.error
    assert str(settings.data_dir) not in failed.error
    with pytest.raises(RepositoryNotReady):
        registry.builder(entry.id)

    state["fail"] = False
    retry = registry.submit("https://github.com/acme/private-repo")
    registry.wait(retry.id)
    assert registry.get(entry.id).status == "ready"


def test_a_failure_does_not_touch_another_repository(tmp_path: Path, store: VectorStore) -> None:
    def clone(url: str, dest: Path, timeout_s: float = 0, hardened: bool = False) -> Path:
        if "broken" in url:
            raise CloneError("git clone failed: nope")
        return fake_clone()(url, dest, timeout_s, hardened)

    registry, _ = make(tmp_path, store, clone=clone)
    good = registry.submit("https://github.com/acme/widgets")
    registry.wait(good.id)
    bad = registry.submit("https://github.com/acme/broken")
    registry.wait(bad.id)
    assert registry.get(good.id).status == "ready" and registry.get(bad.id).status == "failed"
    assert registry.builder(good.id).build("signer", 2, False).evidence


def test_a_repository_without_python_files_fails_clearly(
    tmp_path: Path, store: VectorStore
) -> None:
    registry, _ = make(tmp_path, store, clone=fake_clone({"README.md": "hi", "a.js": "x"}))
    entry = registry.submit("https://github.com/acme/js-only")
    registry.wait(entry.id)
    assert registry.get(entry.id).status == "failed"
    assert "No Python files" in registry.get(entry.id).error


def test_too_many_python_files_fail_before_indexing(tmp_path: Path, store: VectorStore) -> None:
    files = {f"m{i}.py": "x = 1\n" for i in range(6)}
    registry, _ = make(tmp_path, store, clone=fake_clone(files), max_python_files=5)
    entry = registry.submit("https://github.com/acme/huge")
    registry.wait(entry.id)
    assert registry.get(entry.id).status == "failed"
    assert "6 Python files" in registry.get(entry.id).error


def test_an_oversized_clone_is_rejected_and_removed(tmp_path: Path, store: VectorStore) -> None:
    registry, settings = make(
        tmp_path, store, clone=fake_clone({"big.py": "#" * 2_000_000}), max_clone_mb=1
    )
    entry = registry.submit("https://github.com/acme/big")
    registry.wait(entry.id)
    assert registry.get(entry.id).status == "failed" and "MiB" in registry.get(entry.id).error
    assert not (settings.repos_dir / "acme" / "big").exists()


# --- limits and switches ---


def test_indexing_can_be_switched_off(tmp_path: Path, store: VectorStore) -> None:
    registry, _ = make(tmp_path, store, enable_repo_indexing=False)
    with pytest.raises(IndexingDisabled):
        registry.submit("https://github.com/acme/widgets")


def test_the_number_of_repositories_is_capped_and_nothing_is_evicted(
    tmp_path: Path, store: VectorStore
) -> None:
    registry, _ = make(tmp_path, store, max_repositories=2)
    for name in ("one", "two"):
        registry.wait(registry.submit(f"https://github.com/acme/{name}").id)
    with pytest.raises(RegistryFull):
        registry.submit("https://github.com/acme/three")
    assert {e.name for e in registry.list()} == {"acme/one", "acme/two"}
    assert all(e.status == "ready" for e in registry.list())


def test_the_queue_is_bounded(tmp_path: Path, store: VectorStore) -> None:
    gate = threading.Event()

    def slow(url: str, dest: Path, timeout_s: float = 0, hardened: bool = False) -> Path:
        gate.wait(10)
        return fake_clone()(url, dest, timeout_s, hardened)

    registry, _ = make(tmp_path, store, clone=slow, max_queued_jobs=2)
    try:
        registry.submit("https://github.com/acme/a")
        registry.submit("https://github.com/acme/b")
        with pytest.raises(QueueFull):
            registry.submit("https://github.com/acme/c")
    finally:
        gate.set()
        for e in registry.list():
            registry.wait(e.id)


def test_status_moves_through_the_stages(tmp_path: Path, store: VectorStore) -> None:
    seen: list[str] = []
    registry, _ = make(tmp_path, store)

    def clone(url: str, dest: Path, timeout_s: float = 0, hardened: bool = False) -> Path:
        seen.append(registry.get("gh__acme__widgets").status)
        return fake_clone()(url, dest, timeout_s, hardened)

    registry._clone = clone  # noqa: SLF001  (observe the state during the job)
    registry.wait(registry.submit("https://github.com/acme/widgets").id)
    assert seen == ["cloning"] and registry.get("gh__acme__widgets").status == "ready"


# --- lookup and the default index ---


def test_unknown_and_unready_repositories_raise_distinct_errors(
    tmp_path: Path, store: VectorStore
) -> None:
    registry, _ = make(tmp_path, store)
    with pytest.raises(RepositoryNotFound):
        registry.builder("gh__nobody__nothing")
    with pytest.raises(RepositoryNotFound):
        registry.builder(None)  # no default configured


def test_the_default_index_is_listed_and_used_when_no_repository_is_named(
    tmp_path: Path, store: VectorStore
) -> None:
    registry, _ = make(tmp_path, store)
    ready = registry.submit("https://github.com/acme/widgets")
    registry.wait(ready.id)
    builder = registry.builder(ready.id)
    registry.register_default("flask_evidence", "flask_evidence", builder)
    listed = {e["id"]: e for e in (x.public() for x in registry.list())}
    assert (
        listed["flask_evidence"]["default"] is True
        and listed["flask_evidence"]["status"] == "ready"
    )
    assert registry.builder(None) is builder


def test_ready_repositories_have_an_answer_service_only_with_a_model(
    tmp_path: Path, store: VectorStore
) -> None:
    with_model, _ = make(tmp_path / "a", store, llm=FakeLLM(output()))
    entry = with_model.submit("https://github.com/acme/widgets")
    with_model.wait(entry.id)
    assert with_model.service(entry.id) is not None
    without, _ = make(tmp_path / "b", store)
    other = without.submit("https://github.com/acme/widgets")
    without.wait(other.id)
    assert without.service(other.id) is None


def test_repositories_are_reloaded_after_a_restart(tmp_path: Path, store: VectorStore) -> None:
    first, settings = make(tmp_path, store)
    entry = first.submit("https://github.com/acme/widgets")
    first.wait(entry.id)
    second = RepositoryRegistry(settings, store, HashingEmbedder(), clone=fake_clone())
    second.load_existing()
    reloaded = second.get(entry.id)
    assert reloaded and reloaded.status == "ready" and reloaded.name == "acme/widgets"
    assert second.builder(entry.id).build("signer", 2, False).evidence


# --- the job leaves a trail in the server log (the status shown to users is deliberately brief) ---


def test_stages_and_failures_are_logged(
    tmp_path: Path, store: VectorStore, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    def clone(url: str, dest: Path, timeout_s: float = 0, hardened: bool = False) -> Path:
        if "broken" in url:
            raise CloneError("This repository could not be accessed without credentials.")
        return fake_clone()(url, dest, timeout_s, hardened)

    caplog.set_level(logging.INFO, logger="repocompass.indexer")
    registry, _ = make(tmp_path, store, clone=clone)
    registry.wait(registry.submit("https://github.com/acme/widgets").id)
    registry.wait(registry.submit("https://github.com/acme/broken").id)
    text = caplog.text
    assert "gh__acme__widgets" in text and "cloning" in text and "ready" in text
    assert "gh__acme__broken" in text and "failed" in text and "without credentials" in text


def test_an_unexpected_exception_is_logged_with_its_traceback(
    tmp_path: Path, store: VectorStore, caplog: pytest.LogCaptureFixture
) -> None:
    def clone(url: str, dest: Path, timeout_s: float = 0, hardened: bool = False) -> Path:
        raise MemoryError("simulated out of memory in the embedder")

    registry, _ = make(tmp_path, store, clone=clone)
    entry = registry.submit("https://github.com/acme/widgets")
    registry.wait(entry.id)
    assert registry.get(entry.id).error == "Indexing failed (MemoryError)"  # brief for the user
    assert "simulated out of memory" in caplog.text and "Traceback" in caplog.text


# --- a host that is too small fails clearly instead of being killed while indexing ---


def test_too_little_memory_fails_before_the_embedding_model_is_loaded(
    tmp_path: Path, store: VectorStore
) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path / "data")
    registry = RepositoryRegistry(  # no embedder given: loading one would be the heavy step
        settings, store, None, clone=fake_clone(), memory_limit=lambda: 512
    )
    entry = registry.submit("https://github.com/acme/widgets")
    registry.wait(entry.id)
    failed = registry.get(entry.id)
    assert failed.status == "failed" and registry._embedder_ is None  # noqa: SLF001
    assert "512 MiB" in failed.error and "memory" in failed.error
    assert not (settings.repos_dir / "acme" / "widgets").exists()  # nothing left behind


@pytest.mark.parametrize("limit", [None, 4096, 900])
def test_enough_or_unknown_memory_does_not_block_indexing(
    tmp_path: Path, store: VectorStore, limit: int | None
) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path / "data")
    registry = RepositoryRegistry(
        settings, store, HashingEmbedder(), clone=fake_clone(), memory_limit=lambda: limit
    )
    entry = registry.submit("https://github.com/acme/widgets")
    registry.wait(entry.id)
    assert registry.get(entry.id).status == "ready"


def test_the_memory_check_can_be_switched_off(tmp_path: Path, store: VectorStore) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", min_memory_mb=0)
    registry = RepositoryRegistry(
        settings, store, HashingEmbedder(), clone=fake_clone(), memory_limit=lambda: 128
    )
    entry = registry.submit("https://github.com/acme/widgets")
    registry.wait(entry.id)
    assert registry.get(entry.id).status == "ready"
