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


def get_embedder(settings: Settings) -> Embedder:
    return SentenceTransformerEmbedder(
        settings.embedding_model, query_prefix=settings.embedding_query_prefix
    )
