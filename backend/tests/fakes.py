"""Test doubles shared by several test modules."""

import math
import re

DIM = 256


class FakeEmbedder:
    """Hashed bag-of-words embedder: deterministic, no model download."""

    model_name = "fake-bow"
    device = "cpu"
    dimension = DIM

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * DIM
        for word in re.findall(r"[a-z]+", text.lower()):
            vec[sum(ord(c) * 31**i for i, c in enumerate(word)) % DIM] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed_documents(self, texts: list[str], show_progress: bool = False) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)
