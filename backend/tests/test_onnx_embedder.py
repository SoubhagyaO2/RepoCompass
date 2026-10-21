"""The ONNX embedder (fastembed): the same model without torch, so a 512 MiB host can serve it.
Experiments keep the torch embedder (the default); the product image selects this one."""

import math
import sys
import types
from typing import Any

import numpy as np
import pytest

from app.config import Settings
from app.retrieval.embedder import OnnxEmbedder, get_embedder


class FakeTextEmbedding:
    """Stands in for fastembed.TextEmbedding: records calls, returns unnormalised vectors."""

    calls: list[tuple[str, Any]] = []

    def __init__(self, model_name: str, **kwargs: Any) -> None:
        self.model_name = model_name
        FakeTextEmbedding.calls.append(("init", kwargs))

    @staticmethod
    def get_embedding_size(model_name: str) -> int:
        return 4

    def embed(self, texts: list[str], batch_size: int = 256, **_: Any):  # type: ignore[no-untyped-def]
        FakeTextEmbedding.calls.append(("embed", (list(texts), batch_size)))
        for t in texts:
            yield np.array([len(t), 1.0, 2.0, 3.0], dtype=np.float32)


@pytest.fixture
def fake_fastembed(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeTextEmbedding.calls = []
    module = types.ModuleType("fastembed")
    module.TextEmbedding = FakeTextEmbedding  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fastembed", module)


def test_vectors_are_l2_normalised_lists_of_floats(fake_fastembed: None) -> None:
    embedder = OnnxEmbedder("BAAI/bge-small-en-v1.5", query_prefix="")
    vectors = embedder.embed_documents(["abc", "a longer passage"])
    assert len(vectors) == 2 and all(isinstance(v, list) and len(v) == 4 for v in vectors)
    for v in vectors:
        assert math.isclose(math.sqrt(sum(x * x for x in v)), 1.0, rel_tol=1e-6)
    assert embedder.dimension == 4 and embedder.device == "cpu"
    assert embedder.model_name == "BAAI/bge-small-en-v1.5"


def test_memory_settings_are_low_by_default(fake_fastembed: None) -> None:
    embedder = OnnxEmbedder("m")
    embedder.embed_documents(["a", "b", "c"])
    init = next(c[1] for c in FakeTextEmbedding.calls if c[0] == "init")
    batch = next(c[1][1] for c in FakeTextEmbedding.calls if c[0] == "embed")
    assert init == {"enable_cpu_mem_arena": False} and batch == 1


def test_queries_get_the_prefix_and_documents_do_not(fake_fastembed: None) -> None:
    embedder = OnnxEmbedder("m", query_prefix="Q: ")
    embedder.embed_documents(["doc"])
    embedder.embed_query("question")
    embedded = [c[1][0] for c in FakeTextEmbedding.calls if c[0] == "embed"]
    assert embedded == [["doc"], ["Q: question"]]


def test_an_empty_batch_returns_nothing_without_calling_the_model(fake_fastembed: None) -> None:
    embedder = OnnxEmbedder("m")
    FakeTextEmbedding.calls.clear()
    assert embedder.embed_documents([]) == []
    assert FakeTextEmbedding.calls == []


def test_get_embedder_selects_the_backend_from_settings(
    fake_fastembed: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    onnx = get_embedder(Settings(_env_file=None, embedder_backend="onnx"))
    assert isinstance(onnx, OnnxEmbedder)
    with pytest.raises(ValueError, match="embedder_backend"):
        get_embedder(Settings(_env_file=None, embedder_backend="nonsense"))


def test_the_default_backend_stays_torch_so_experiments_are_unchanged() -> None:
    assert Settings(_env_file=None).embedder_backend == "torch"


def test_the_memory_a_host_needs_depends_on_the_backend() -> None:
    assert Settings(_env_file=None).required_memory_mb == 900
    assert Settings(_env_file=None, embedder_backend="onnx").required_memory_mb == 450
    assert (
        Settings(_env_file=None, embedder_backend="onnx", min_memory_mb=700).required_memory_mb
        == 700
    )
    assert Settings(_env_file=None, min_memory_mb=0).required_memory_mb == 0


def test_a_missing_package_gives_an_actionable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "fastembed", None)  # import fails
    with pytest.raises(RuntimeError, match="fastembed"):
        OnnxEmbedder("m")


def test_the_real_model_matches_sentence_transformers() -> None:
    """Integration: skipped unless fastembed and the model are available offline."""
    pytest.importorskip("fastembed")
    pytest.importorskip("sentence_transformers")
    from app.retrieval.embedder import SentenceTransformerEmbedder

    texts = ["def verify_signature(self, value, sig):\n    return True", "where is it checked?"]
    onnx = OnnxEmbedder("BAAI/bge-small-en-v1.5")
    torch_ = SentenceTransformerEmbedder("BAAI/bge-small-en-v1.5")
    for a, b in zip(onnx.embed_documents(texts), torch_.embed_documents(texts), strict=True):
        assert float(np.dot(a, b)) > 0.9999
