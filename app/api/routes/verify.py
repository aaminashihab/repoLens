"""API route for Evidence-Based Repository Verification."""

import logging
import os
from fastapi import APIRouter, Depends, HTTPException, status, Request

from app.api.dependencies import (
    get_verification_service,
    require_api_key,
    limiter,
)
from app.models.verification import VerificationReport, VerificationRequest
from app.services.retrieval_service import IndexNotFoundError
from app.services.verification_service import (
    VerificationService,
    VerificationServiceError,
)

VERIFY_RATE_LIMIT = os.getenv("VERIFY_RATE_LIMIT", "30/minute")


def get_verify_rate_limit() -> str:
    return VERIFY_RATE_LIMIT


router = APIRouter(tags=["verification"], dependencies=[Depends(require_api_key)])
logger = logging.getLogger(__name__)


@router.post(
    "/verify",
    response_model=VerificationReport,
    summary="Verify a repository claim",
    description="Verify a security, PR, or architectural claim against an indexed repository using hybrid vector+graph evidence retrieval.",
)
@limiter.limit(get_verify_rate_limit)
def verify_claim(
    request: Request,
    verification_request: VerificationRequest,
    service: VerificationService = Depends(get_verification_service),
) -> VerificationReport:
    """Verify a claim about an indexed code repository and return an evidence report."""
    try:
        return service.verify_claim(
            index_id=verification_request.index_id,
            claim=verification_request.claim,
            repository_url=verification_request.repository_url,
            pr_number=verification_request.pr_number,
            issue_number=verification_request.issue_number,
        )
    except IndexNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except VerificationServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except Exception as exc:
        logger.exception("Verification request failed unexpectedly")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal server error occurred while processing the verification request.",
        ) from exc
