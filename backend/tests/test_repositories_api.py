"""The HTTP interface for connecting repositories, and the `repository` selector on the evidence
and answer endpoints."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from answer_fixtures import FakeLLM, claim, output
from fastapi.testclient import TestClient
from test_repositories import fake_clone

from app.config import Settings
from app.main import create_app
from app.repositories import RepositoryRegistry
from app.retrieval.embedder import HashingEmbedder
from app.retrieval.vector_store import VectorStore


@pytest.fixture
def store(tmp_path: Path) -> Iterator[VectorStore]:
    with VectorStore(tmp_path / "qdrant") as s:
        yield s


def client_with(
    tmp_path: Path, store: VectorStore, llm: Any = None, clone: Any = None, **over: Any
) -> tuple[TestClient, RepositoryRegistry]:
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", **over)
    registry = RepositoryRegistry(
        settings, store, HashingEmbedder(), llm=llm, clone=clone or fake_clone()
    )
    return TestClient(create_app(registry=registry)), registry


def connect(client: TestClient, registry: RepositoryRegistry, url: str) -> dict[str, Any]:
    response = client.post("/v1/repositories", json={"url": url})
    assert response.status_code == 202, response.text
    body = response.json()
    registry.wait(body["id"])
    return body


def test_post_returns_202_and_the_repository_becomes_ready(
    tmp_path: Path, store: VectorStore
) -> None:
    with client_with(tmp_path, store)[0] as client:
        registry = client.app.state.registry
        first = client.post("/v1/repositories", json={"url": "https://github.com/acme/widgets"})
        assert first.status_code == 202
        body = first.json()
        assert body["id"] == "gh__acme__widgets" and body["name"] == "acme/widgets"
        registry.wait(body["id"])
        status = client.get(f"/v1/repositories/{body['id']}").json()
        assert status["status"] == "ready" and status["files"] == 3 and status["error"] is None
        listed = client.get("/v1/repositories").json()
        assert [r["id"] for r in listed] == [body["id"]]


@pytest.mark.parametrize(
    "url",
    ["http://github.com/o/r", "https://gitlab.com/o/r", "https://127.0.0.1/o/r", "not a url", ""],
)
def test_invalid_urls_are_422_with_a_readable_message(
    tmp_path: Path, store: VectorStore, url: str
) -> None:
    with client_with(tmp_path, store)[0] as client:
        response = client.post("/v1/repositories", json={"url": url})
        assert response.status_code == 422 and response.json()["detail"]


def test_a_missing_or_oversized_url_field_is_rejected(tmp_path: Path, store: VectorStore) -> None:
    with client_with(tmp_path, store)[0] as client:
        assert client.post("/v1/repositories", json={}).status_code == 422
        long_url = "https://github.com/o/" + "r" * 400
        assert client.post("/v1/repositories", json={"url": long_url}).status_code == 422


def test_disabled_indexing_is_403_and_the_health_check_says_so(
    tmp_path: Path, store: VectorStore
) -> None:
    with client_with(tmp_path, store, enable_repo_indexing=False)[0] as client:
        response = client.post("/v1/repositories", json={"url": "https://github.com/o/r"})
        assert response.status_code == 403
        assert client.get("/health").json()["indexing_enabled"] is False


def test_limits_map_to_distinct_status_codes(tmp_path: Path, store: VectorStore) -> None:
    client, registry = client_with(tmp_path, store, max_repositories=1)
    with client:
        connect(client, registry, "https://github.com/acme/one")
        full = client.post("/v1/repositories", json={"url": "https://github.com/acme/two"})
        assert full.status_code == 409 and "limit" in full.json()["detail"]


def test_unknown_repository_status_is_404(tmp_path: Path, store: VectorStore) -> None:
    with client_with(tmp_path, store)[0] as client:
        assert client.get("/v1/repositories/gh__nobody__nothing").status_code == 404
        assert client.get("/v1/repositories/..%2F..%2Fetc").status_code in (404, 422)


def test_a_failed_job_reports_an_error_not_a_server_error(
    tmp_path: Path, store: VectorStore
) -> None:
    client, registry = client_with(tmp_path, store, clone=fake_clone({"a.js": "x"}))
    with client:
        body = connect(client, registry, "https://github.com/acme/js")
        status = client.get(f"/v1/repositories/{body['id']}").json()
        assert status["status"] == "failed" and "No Python files" in status["error"]


# --- evidence and answers for a chosen repository -------------------------------------------------


def test_evidence_is_served_for_the_chosen_repository(tmp_path: Path, store: VectorStore) -> None:
    client, registry = client_with(tmp_path, store)
    with client:
        body = connect(client, registry, "https://github.com/acme/widgets")
        ok = client.get("/v1/evidence", params={"q": "signer verify", "repository": body["id"]})
        assert ok.status_code == 200 and ok.json()["evidence"][0]["file"].startswith("pkg/")
        assert (
            client.get("/v1/evidence", params={"q": "x", "repository": "gh__no__pe"}).status_code
            == 404
        )
        assert client.get("/v1/evidence", params={"q": "x"}).status_code == 503  # no default


def test_a_repository_that_is_not_ready_is_409(tmp_path: Path, store: VectorStore) -> None:
    client, registry = client_with(tmp_path, store, clone=fake_clone({"a.js": "x"}))
    with client:
        body = connect(client, registry, "https://github.com/acme/js")
        response = client.get("/v1/evidence", params={"q": "x", "repository": body["id"]})
        assert response.status_code == 409


def test_answers_use_the_chosen_repository_and_name_its_evidence(
    tmp_path: Path, store: VectorStore
) -> None:
    llm = FakeLLM(output(claims=[claim("The signer verifies values.", "E1")]))
    client, registry = client_with(tmp_path, store, llm=llm)
    with client:
        body = connect(client, registry, "https://github.com/acme/widgets")
        response = client.post(
            "/v1/answer", json={"question": "Where is the signer verify?", "repository": body["id"]}
        )
        assert response.status_code == 200
        answer = response.json()
        assert answer["status"] == "answered" and answer["validation"]["valid"]
        assert answer["claims"][0]["citations"][0]["file"].startswith("pkg/")


def test_answering_without_a_model_is_503(tmp_path: Path, store: VectorStore) -> None:
    client, registry = client_with(tmp_path, store)
    with client:
        body = connect(client, registry, "https://github.com/acme/widgets")
        response = client.post("/v1/answer", json={"question": "x", "repository": body["id"]})
        assert response.status_code == 503


def test_health_reports_ready_repositories_and_answers(tmp_path: Path, store: VectorStore) -> None:
    client, registry = client_with(tmp_path, store, llm=FakeLLM(output()))
    with client:
        assert client.get("/health").json()["index_loaded"] is False
        connect(client, registry, "https://github.com/acme/widgets")
        body = client.get("/health").json()
        assert body["index_loaded"] is True and body["answer_enabled"] is True
        assert body["indexing_enabled"] is True


def test_the_503_messages_say_what_is_missing(tmp_path: Path, store: VectorStore) -> None:
    client, registry = client_with(tmp_path, store)  # no model, no default repository
    with client:
        body = connect(client, registry, "https://github.com/acme/widgets")
        no_repo = client.post("/v1/answer", json={"question": "x"})
        assert no_repo.status_code == 503 and "repository" in no_repo.json()["detail"]
        no_model = client.post("/v1/answer", json={"question": "x", "repository": body["id"]})
        assert no_model.status_code == 503 and "GEMINI_API_KEY" in no_model.json()["detail"]
