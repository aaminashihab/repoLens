import logging
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status

from app.models.repository import RepositoryIndexRequest, RepositoryIndexResponse
from app.services.repository_service import (
    InvalidRepositoryUrlError,
    RepositoryCloneError,
    RepositoryService,
)


router = APIRouter(tags=["repositories"])
logger = logging.getLogger(__name__)
repository_service = RepositoryService()


@router.post(
    "/index-repository",
    response_model=RepositoryIndexResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start repository indexing",
)
def index_repository(request: RepositoryIndexRequest) -> RepositoryIndexResponse:
    """Clone a repository and create its pending indexing job reference."""
    try:
        repository_path = repository_service.clone_repository(str(request.repo_url))
    except InvalidRepositoryUrlError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RepositoryCloneError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    index_id = uuid4()
    logger.info(
        "Repository accepted for indexing",
        extra={"index_id": str(index_id), "repository_path": str(repository_path)},
    )
    # Parsing and embedding generation will be introduced by the indexing pipeline.
    return RepositoryIndexResponse(index_id=index_id, status="processing")
