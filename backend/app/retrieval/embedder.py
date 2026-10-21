"""Text -> vector. Wrapped behind a small interface so the model stays configurable."""

from typing import Protocol

from app.config import Settings


class Embedder(Protocol):
    model_name: str
    device: str
    dimension: int

    def embed_documents(self, texts: list[str], show_progress: bool = False) -> list[list[float]]:
        """Embed passages to be indexed. Vectors are L2-normalised (cosine == dot product)."""
        ...

    def embed_query(self, text: str) -> list[float]:
        """Embed a search query (some models want a task prefix here)."""
        ...


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str, query_prefix: str = "", batch_size: int = 32) -> None:
        # Imported here: sentence-transformers pulls in torch, which takes seconds to import.
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name)
        self._query_prefix = query_prefix
        self._batch_size = batch_size
        self.model_name = model_name
        self.device = str(self._model.device)
        self.dimension = int(self._model.get_embedding_dimension() or 0)
        # Input beyond this many tokens is silently cut off before embedding.
        self.max_seq_length = int(self._model.max_seq_length or 0)

    def count_tokens(self, texts: list[str]) -> list[int]:
        """Token count of each text, without truncation (to see what the model cannot read)."""
        encoded = self._model.tokenizer(texts, truncation=False, verbose=False)
        return [len(ids) for ids in encoded["input_ids"]]

    def embed_documents(self, texts: list[str], show_progress: bool = False) -> list[list[float]]:
        vectors = self._model.encode(
            texts,
            batch_size=self._batch_size,
            normalize_embeddings=True,
            show_progress_bar=show_progress,
            convert_to_numpy=True,
        )
        return vectors.tolist()

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([self._query_prefix + text])[0]


class OnnxEmbedder:
    """The same model through ONNX Runtime (fastembed), without torch: about 300 MiB resident
    instead of 600 or more, so a 512 MiB host can serve it. Vectors match the torch embedder to
    about six decimals (cosine 0.999999 on a check), so existing indexes remain usable.

    Batch size 1 and no memory arena are deliberate. Batches are padded to their longest text, and
    code chunks vary a lot in length, so larger batches use far more memory and are slower
    (measured on 81 chunks: batch 16 peaked at 785 MiB in 26 s; batch 1 at 283 MiB in 5 s)."""

    device = "cpu"

    def __init__(self, model_name: str, query_prefix: str = "", batch_size: int = 1) -> None:
        try:
            from fastembed import TextEmbedding
        except ImportError as error:  # also raised when the module is blocked
            raise RuntimeError(
                "the onnx embedder needs the fastembed package: pip install 'repocompass[onnx]'"
            ) from error
        self._model = TextEmbedding(model_name, enable_cpu_mem_arena=False)
        self._query_prefix = query_prefix
        self._batch_size = batch_size
        self.model_name = model_name
        self.dimension = int(TextEmbedding.get_embedding_size(model_name))

    def embed_documents(self, texts: list[str], show_progress: bool = False) -> list[list[float]]:
        if not texts:
            return []
        import numpy as np

        vectors = np.array(list(self._model.embed(texts, batch_size=self._batch_size)), dtype=float)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        return (vectors / np.where(norms == 0, 1.0, norms)).tolist()

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([self._query_prefix + text])[0]


def get_embedder(settings: Settings) -> Embedder:
    backend = settings.embedder_backend
    if backend == "onnx":
        return OnnxEmbedder(settings.embedding_model, query_prefix=settings.embedding_query_prefix)
    if backend == "torch":
        return SentenceTransformerEmbedder(
            settings.embedding_model, query_prefix=settings.embedding_query_prefix
        )
    raise ValueError(f"unknown embedder_backend {backend!r}; use 'torch' or 'onnx'")


class HashingEmbedder:
    """A cheap deterministic stand-in embedder (hashed bag of words, no model download).

    For tests and for reference builds whose checks do not depend on the embedding model.
    """

    model_name = "hashing-bow"
    device = "cpu"
    dimension = 256

    def _embed(self, text: str) -> list[float]:
        import math
        import re

        vec = [0.0] * self.dimension
        for word in re.findall(r"[a-z]+", text.lower()):
            vec[sum(ord(c) * 31**i for i, c in enumerate(word)) % self.dimension] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed_documents(self, texts: list[str], show_progress: bool = False) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class SeededEmbedder:
    """Distinct texts get distinct pseudo-random unit vectors (no accidental score ties; the
    bag-of-words stand-in produces many). Identical texts still get identical vectors.

    For tests and runs that check evidence logic rather than the embedding model."""

    model_name = "seeded-random"
    device = "cpu"
    dimension = 32

    def _vector(self, text: str) -> list[float]:
        import hashlib
        import random

        rng = random.Random(hashlib.sha256(text.encode()).digest())
        v = [rng.gauss(0, 1) for _ in range(self.dimension)]
        norm = sum(x * x for x in v) ** 0.5
        return [x / norm for x in v]

    def embed_documents(self, texts: list[str], show_progress: bool = False) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector("query:" + text)
