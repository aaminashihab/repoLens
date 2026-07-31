"""API route for the Memory/Space Efficiency Scan layer."""

import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.dependencies import (
    get_memory_scan_service,
    limiter,
    require_api_key,
)
from app.models.memory_scan import MemoryScanReport, MemoryScanRequest
from app.services.memory_scan_service import (
    MemoryScanIndexNotFoundError,
    MemoryScanService,
    MemoryScanServiceError,
)

MEMORY_SCAN_RATE_LIMIT = os.getenv("MEMORY_SCAN_RATE_LIMIT", "10/minute")


def get_memory_scan_rate_limit() -> str:
    return MEMORY_SCAN_RATE_LIMIT


router = APIRouter(tags=["memory-scan"], dependencies=[Depends(require_api_key)])
logger = logging.getLogger(__name__)


@router.post(
    "/memory-scan",
    response_model=MemoryScanReport,
    summary="Scan a repository for memory/space inefficiencies",
    description=(
        "Scan an indexed repository for memory- and space-wasting code patterns "
        "(unbounded accumulation, unbounded caches, full-file loads, missing "
        "pagination, string concatenation in loops, etc.) using static heuristics "
        "confirmed by an LLM-as-Judge review, same evidence-driven approach as /verify."
    ),
)
@limiter.limit(get_memory_scan_rate_limit)
def scan_memory(
    request: Request,
    scan_request: MemoryScanRequest,
    service: MemoryScanService = Depends(get_memory_scan_service),
) -> MemoryScanReport:
    """Run a memory/space efficiency scan against an indexed repository."""
    try:
        return service.scan(
            index_id=scan_request.index_id,
            max_findings=scan_request.max_findings,
            min_severity=scan_request.min_severity,
        )
    except MemoryScanIndexNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except MemoryScanServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except Exception as exc:
        logger.exception("Memory scan request failed unexpectedly")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal server error occurred while processing the memory scan request.",
        ) from exc
