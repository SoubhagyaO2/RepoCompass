import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.retrieval.vector_store import VectorStore


@pytest.fixture
def store(tmp_path: Path) -> Iterator[VectorStore]:
    with VectorStore(tmp_path / "q") as s:
        yield s


def ids(n: int) -> list[str]:
    return [str(uuid.uuid4()) for _ in range(n)]


def put(store: VectorStore, name: str, point_ids: list[str]) -> None:
    store.upsert_points(
        name, point_ids, [[1.0, float(i)] for i in range(len(point_ids))],
        [{"n": i} for i in range(len(point_ids))],
    )  # fmt: skip


def test_recreating_a_collection_really_empties_it(store: VectorStore) -> None:
    """Regression: qdrant-client's local mode brings old points back after delete + create."""
    store.recreate_collection("c", 2)
    put(store, "c", ids(5))
    assert store.count("c") == 5
    store.recreate_collection("c", 2)
    assert store.count("c") == 0 and store.all_points("c") == {}


def test_dropping_and_ensuring_a_collection_starts_empty(store: VectorStore) -> None:
    store.ensure_collection("c", 2)
    put(store, "c", ids(4))
    store.drop_collection("c")
    assert not store.collection_exists("c")
    store.ensure_collection("c", 2)
    assert store.count("c") == 0


def test_ensure_collection_never_touches_an_existing_one(store: VectorStore) -> None:
    store.ensure_collection("c", 2)
    put(store, "c", ids(3))
    store.ensure_collection("c", 2)
    assert store.count("c") == 3


def test_point_operations_by_stable_id(store: VectorStore) -> None:
    store.ensure_collection("c", 2)
    a, b, c = ids(3)
    put(store, "c", [a, b, c])
    store.upsert_points("c", [a], [[0.0, 1.0]], [{"n": "a2"}])  # idempotent overwrite
    assert store.count("c") == 3
    store.delete_points("c", [b])
    store.delete_points("c", [])
    points = store.all_points("c", with_vectors=True)
    assert set(points) == {a, c} and points[a][0] == {"n": "a2"} and points[a][1] is not None
    assert set(store.get_vectors("c", [a, b, str(uuid.uuid4())])) == {a}  # missing ids are absent
    assert store.get_vectors("c", []) == {}
