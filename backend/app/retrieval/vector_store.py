"""A thin wrapper around Qdrant (local, in-process mode) so the rest of the code never
touches the client API directly."""

import threading
from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointIdsList, PointStruct, VectorParams

UPSERT_BATCH = 256


class _Locked:
    """Serialises every method call on a client: the API threads and the indexing worker share one
    local Qdrant client, which is a single-process store."""

    def __init__(self, client: Any) -> None:
        self._inner = client
        self._lock = threading.RLock()

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._inner, name)
        if not callable(attribute):
            return attribute

        def call(*args: Any, **kwargs: Any) -> Any:
            with self._lock:
                return attribute(*args, **kwargs)

        return call


class VectorStore:
    def __init__(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        self._client = _Locked(QdrantClient(path=str(path)))

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "VectorStore":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def collection_exists(self, name: str) -> bool:
        return self._client.collection_exists(name)

    def _purge(self, name: str) -> None:
        """Delete every point of a collection.

        Needed because in qdrant-client's local mode, deleting a collection and creating one of
        the same name again brings the old points back (the storage on disk is reused). Always
        empty a collection explicitly before dropping it.
        """
        ids: list[Any] = []
        offset = None
        while True:
            records, offset = self._client.scroll(
                name, limit=1024, offset=offset, with_payload=False, with_vectors=False
            )
            ids += [r.id for r in records]
            if offset is None:
                break
        for i in range(0, len(ids), 1024):
            self._client.delete(name, points_selector=PointIdsList(points=ids[i : i + 1024]))

    def recreate_collection(self, name: str, dimension: int) -> None:
        """Create an empty cosine-distance collection, replacing any existing one."""
        if self._client.collection_exists(name):
            self._purge(name)
            self._client.delete_collection(name)
        self._client.create_collection(
            name, vectors_config=VectorParams(size=dimension, distance=Distance.COSINE)
        )
        self._purge(name)  # in case a previous incarnation's storage was picked up

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

    # --- incremental updates: points keyed by stable ids (UUID strings) -----------------------

    def ensure_collection(self, name: str, dimension: int) -> None:
        """Create the collection if it does not exist; never touches an existing one."""
        if not self._client.collection_exists(name):
            self._client.create_collection(
                name, vectors_config=VectorParams(size=dimension, distance=Distance.COSINE)
            )

    def drop_collection(self, name: str) -> None:
        if self._client.collection_exists(name):
            self._purge(name)  # see _purge: deleting the collection alone leaves its points
            self._client.delete_collection(name)

    def upsert_points(
        self,
        name: str,
        ids: list[str],
        vectors: list[list[float]],
        payloads: list[dict[str, Any]],
    ) -> None:
        """Insert or overwrite points by id (idempotent)."""
        for i in range(0, len(ids), UPSERT_BATCH):
            points = [
                PointStruct(id=pid, vector=vector, payload=payload)
                for pid, vector, payload in zip(
                    ids[i : i + UPSERT_BATCH],
                    vectors[i : i + UPSERT_BATCH],
                    payloads[i : i + UPSERT_BATCH],
                    strict=True,
                )
            ]
            self._client.upsert(name, points=points)

    def delete_points(self, name: str, ids: list[str]) -> None:
        if ids:
            self._client.delete(name, points_selector=PointIdsList(points=list(ids)))

    def get_vectors(self, name: str, ids: list[str]) -> dict[str, list[float]]:
        """Stored vectors of the given points; ids that do not exist are simply absent."""
        if not ids:
            return {}
        records = self._client.retrieve(name, ids=list(ids), with_vectors=True, with_payload=False)
        return {str(r.id): [float(x) for x in r.vector] for r in records}  # type: ignore[union-attr]

    def all_points(
        self, name: str, with_vectors: bool = False
    ) -> dict[str, tuple[dict[str, Any], list[float] | None]]:
        """Every point as {id: (payload, vector or None)} (for verification, not for serving)."""
        points: dict[str, tuple[dict[str, Any], list[float] | None]] = {}
        offset = None
        while True:
            records, offset = self._client.scroll(
                name, limit=256, offset=offset, with_payload=True, with_vectors=with_vectors
            )
            for r in records:
                vector = [float(x) for x in r.vector] if with_vectors else None  # type: ignore[union-attr]
                points[str(r.id)] = (r.payload or {}, vector)
            if offset is None:
                return points

    def point_ids(self, name: str) -> set[str]:
        """The ids of every point (no payloads or vectors are transferred)."""
        found: set[str] = set()
        offset = None
        while True:
            records, offset = self._client.scroll(
                name, limit=1024, offset=offset, with_payload=False, with_vectors=False
            )
            found |= {str(r.id) for r in records}
            if offset is None:
                return found
