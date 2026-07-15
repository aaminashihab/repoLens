import logging
from time import perf_counter
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status

from app.models.repository import RepositoryIndexRequest, RepositoryIndexResponse
from app.services.clone_service import (
    CloneService,
    InvalidRepositoryUrlError,
    RepositoryCloneError,
)
from app.services.chunk_service import ChunkService, RepositoryChunkError
from app.services.embedding_service import EmbeddingService, EmbeddingServiceError
from app.services.index_service import IndexService, IndexServiceError


router = APIRouter(tags=["repositories"])
logger = logging.getLogger(__name__)
repository_service = CloneService()
chunk_service = ChunkService()
embedding_service = EmbeddingService()
index_service = IndexService()


@router.post(
    "/index-repository",
    response_model=RepositoryIndexResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start repository indexing",
)
def index_repository(request: RepositoryIndexRequest) -> RepositoryIndexResponse:
    """Clone a repository, index its Python symbols, and create a job reference."""
    started_at = perf_counter()
    index_id = uuid4()
    try:
        repository_path = repository_service.clone_repository(str(request.repo_url))
    except InvalidRepositoryUrlError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RepositoryCloneError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    try:
        chunks = chunk_service.index_repository(repository_path)
    except RepositoryChunkError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    try:
        embedded_chunks = embedding_service.embed_chunks(chunks)
        index_service.build_index(str(index_id), embedded_chunks)
    except (EmbeddingServiceError, IndexServiceError) as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    logger.info(
        "Repository indexing completed",
        extra={
            "index_id": str(index_id),
            "repository_path": str(repository_path),
            "chunk_count": len(chunks),
            "total_indexing_time_seconds": perf_counter() - started_at,
        },
    )
    return RepositoryIndexResponse(index_id=index_id, status="processing")
