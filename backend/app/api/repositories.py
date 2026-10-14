"""HTTP interface for connecting repositories by URL and checking their indexing status."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request
from pydantic import BaseModel, Field

from app.ingestion.url import InvalidRepoURLError
from app.repositories import (
    IndexingDisabled,
    QueueFull,
    RegistryFull,
    RepositoryRegistry,
)

router = APIRouter(prefix="/v1", tags=["repositories"])

MAX_URL_CHARS = 300


class ConnectRequest(BaseModel):
    url: Annotated[str, Field(min_length=1, max_length=MAX_URL_CHARS)]


def get_registry(request: Request) -> RepositoryRegistry:
    registry: RepositoryRegistry | None = getattr(request.app.state, "registry", None)
    if registry is None:
        raise HTTPException(503, "repositories are not available")
    return registry


@router.post("/repositories", status_code=202)
def connect_repository(body: ConnectRequest, request: Request) -> dict[str, object]:
    """Validate a public GitHub URL and start indexing it in the background (202). Asking again
    for a repository that is already indexed or being indexed returns it unchanged."""
    registry = get_registry(request)
    try:
        return registry.submit(body.url).public()
    except InvalidRepoURLError as error:
        raise HTTPException(422, str(error)) from error
    except IndexingDisabled as error:
        raise HTTPException(403, str(error)) from error
    except QueueFull as error:
        raise HTTPException(429, str(error)) from error
    except RegistryFull as error:
        raise HTTPException(409, str(error)) from error


@router.get("/repositories")
def list_repositories(request: Request) -> list[dict[str, object]]:
    return [entry.public() for entry in get_registry(request).list()]


@router.get("/repositories/{repository_id}")
def repository_status(
    repository_id: Annotated[str, Path(pattern=r"^[A-Za-z0-9_.-]{1,200}$")], request: Request
) -> dict[str, object]:
    entry = get_registry(request).get(repository_id)
    if entry is None:
        raise HTTPException(404, "unknown repository")
    return entry.public()
