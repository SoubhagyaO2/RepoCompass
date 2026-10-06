"""A thin wrapper around Qdrant (local, in-process mode) so the rest of the code never
touches the client API directly."""

from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

UPSERT_BATCH = 256


class VectorStore:
    def __init__(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        self._client = QdrantClient(path=str(path))

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "VectorStore":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def collection_exists(self, name: str) -> bool:
        return self._client.collection_exists(name)

    def recreate_collection(self, name: str, dimension: int) -> None:
        """Create an empty cosine-distance collection, replacing any existing one."""
        if self._client.collection_exists(name):
            self._client.delete_collection(name)
        self._client.create_collection(
            name, vectors_config=VectorParams(size=dimension, distance=Distance.COSINE)
        )

    def upsert(self, name: str, vectors: list[list[float]], payloads: list[dict[str, Any]]) -> None:
        for i in range(0, len(vectors), UPSERT_BATCH):
            points = [
                PointStruct(id=i + j, vector=vector, payload=payload)
                for j, (vector, payload) in enumerate(
                    zip(vectors[i : i + UPSERT_BATCH], payloads[i : i + UPSERT_BATCH], strict=True)
                )
            ]
            self._client.upsert(name, points=points)

    def search(self, name: str, vector: list[float], k: int) -> list[tuple[float, dict[str, Any]]]:
        """Return up to k (cosine similarity, payload) pairs, best first."""
        response = self._client.query_points(name, query=vector, limit=k, with_payload=True)
        return [(point.score, point.payload or {}) for point in response.points]

    def count(self, name: str) -> int:
        return self._client.count(name, exact=True).count
