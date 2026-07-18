import logging
import os
from time import perf_counter
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status, Request

from app.api.dependencies import (
    get_clone_service,
    get_chunk_service,
    get_embedding_service,
    get_index_service,
    get_job_service,
    require_api_key,
    limiter,
)
from app.models.repository import RepositoryIndexRequest, RepositoryIndexResponse
from app.services.clone_service import (
    CloneService,
    InvalidRepositoryUrlError,
    RepositoryCloneError,
)
from app.services.chunk_service import ChunkService, RepositoryChunkError
from app.services.embedding_service import EmbeddingService, EmbeddingServiceError
from app.services.index_service import IndexService, IndexServiceError
from app.services.job_service import JobService

INDEX_RATE_LIMIT = os.getenv("INDEX_RATE_LIMIT", "10/hour")

def get_index_rate_limit() -> str:
    return INDEX_RATE_LIMIT

router = APIRouter(tags=["repositories"], dependencies=[Depends(require_api_key)])
logger = logging.getLogger(__name__)


def _run_indexing_job(
    repo_url: str,
    index_id: str,
    repository_service: CloneService,
    chunk_service: ChunkService,
    embedding_service: EmbeddingService,
    index_service: IndexService,
    job_service: JobService,
    github_token: str | None = None,
) -> None:
    started_at = perf_counter()
    try:
        with repository_service.clone_repository_context(repo_url, github_token=github_token) as repository_path:
            try:
                chunks = chunk_service.index_repository(repository_path)
            except RepositoryChunkError as exc:
                logger.error(f"Failed to chunk repository: {exc}")
                job_service.update_job_status(index_id, "failed", str(exc))
                return
    except RepositoryCloneError as exc:
        logger.error(f"Failed to clone repository: {exc}")
        job_service.update_job_status(index_id, "failed", str(exc))
        return

    try:
        embedded_chunks = embedding_service.embed_chunks(chunks)
        index_service.build_index(index_id, embedded_chunks, repo_url=repo_url)
    except (EmbeddingServiceError, IndexServiceError) as exc:
        logger.error(f"Failed to build index: {exc}")
        job_service.update_job_status(index_id, "failed", str(exc))
        return

    job_service.update_job_status(index_id, "completed")

    logger.info(
        "Repository indexing completed",
        extra={
            "index_id": index_id,
            "repository_path": str(repository_path),
            "chunk_count": len(chunks),
            "total_indexing_time_seconds": perf_counter() - started_at,
        },
    )


@router.post(
    "/index-repository",
    response_model=RepositoryIndexResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start repository indexing",
)
@limiter.limit(get_index_rate_limit)
def index_repository(
    request: Request,
    index_request: RepositoryIndexRequest,
    background_tasks: BackgroundTasks,
    repository_service: CloneService = Depends(get_clone_service),
    chunk_service: ChunkService = Depends(get_chunk_service),
    embedding_service: EmbeddingService = Depends(get_embedding_service),
    index_service: IndexService = Depends(get_index_service),
    job_service: JobService = Depends(get_job_service),
) -> RepositoryIndexResponse:
    """Clone a repository, index its Python symbols, and create a job reference."""
    try:
        CloneService.validate_github_url(str(index_request.repo_url))
    except InvalidRepositoryUrlError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    index_id = uuid4()
    job_service.update_job_status(str(index_id), "processing")
    background_tasks.add_task(
        _run_indexing_job,
        str(index_request.repo_url),
        str(index_id),
        repository_service,
        chunk_service,
        embedding_service,
        index_service,
        job_service,
        github_token=index_request.github_token,
    )
    
    return RepositoryIndexResponse(index_id=index_id, status="processing")


@router.get(
    "/index-repository/{index_id}",
    summary="Get repository indexing status",
)
def get_indexing_status(
    index_id: str,
    job_service: JobService = Depends(get_job_service),
) -> dict:
    try:
        status_data = job_service.get_job_status(index_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not status_data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return status_data

@router.get(
    "/indexes",
    summary="List all indexed repositories",
    response_model=list[dict],
)
def list_repository_indexes(
    index_service: IndexService = Depends(get_index_service),
) -> list[dict]:
    return index_service.list_indexes()

@router.delete(
    "/index-repository/{index_id}",
    summary="Delete an indexed repository and its job status",
)
def delete_repository_index(
    index_id: str,
    index_service: IndexService = Depends(get_index_service),
    job_service: JobService = Depends(get_job_service),
) -> dict:
    try:
        deleted_index = index_service.delete_index(index_id)
        job_service.delete_job_status(index_id)
    except (IndexServiceError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    
    if not deleted_index:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Index not found")
        
    return {"message": "Index and job status deleted successfully"}


def run_indexes_cleanup(
    ttl_hours: float,
    index_service: IndexService,
    job_service: JobService,
) -> list[str]:
    """Helper to clean up expired indexes and delete corresponding job statuses."""
    deleted_ids = index_service.delete_expired_indexes(ttl_hours)
    for index_id in deleted_ids:
        try:
            job_service.delete_job_status(index_id)
        except ValueError:
            pass
    return deleted_ids


@router.post(
    "/indexes/cleanup",
    summary="Clean up expired indexes immediately",
)
def cleanup_expired_indexes(
    index_service: IndexService = Depends(get_index_service),
    job_service: JobService = Depends(get_job_service),
) -> dict:
    """Run index cleanup and return list of deleted index IDs."""
    ttl_hours = float(os.getenv("INDEX_TTL_HOURS", "168"))
    deleted = run_indexes_cleanup(ttl_hours, index_service, job_service)
    return {"deleted": deleted}

