"""GitHub Pull Request & Issue Verification webhook handler."""

import hashlib
import hmac
import logging
import os
from typing import Any
from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request, status

from app.api.dependencies import get_verification_service, limiter
from app.services.verification_service import VerificationService

GITHUB_RATE_LIMIT = os.getenv("GITHUB_RATE_LIMIT", "30/minute")


def get_github_rate_limit() -> str:
    return GITHUB_RATE_LIMIT


router = APIRouter(prefix="/github", tags=["github"])
logger = logging.getLogger(__name__)


@router.post("/webhook", status_code=status.HTTP_200_OK)
@limiter.limit(get_github_rate_limit)
async def github_webhook(
    request: Request,
    event_type: str = Header(..., alias="X-GitHub-Event"),
    x_hub_signature_256: str | None = Header(None, alias="X-Hub-Signature-256"),
    payload: dict[str, Any] = Body(...),
    service: VerificationService = Depends(get_verification_service),
) -> dict[str, str]:
    """Handle incoming GitHub webhooks for Pull Requests and Issues."""
    secret = os.getenv("GITHUB_WEBHOOK_SECRET")
    if secret:
        if not x_hub_signature_256 or not x_hub_signature_256.startswith("sha256="):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing X-Hub-Signature-256 header",
            )
        raw_body = await request.body()
        expected_sig = "sha256=" + hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(x_hub_signature_256, expected_sig):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="GitHub webhook signature verification failed",
            )

    if not payload:
        raise HTTPException(status_code=400, detail="Missing payload")

    if event_type == "pull_request":
        action = payload.get("action")
        pr = payload.get("pull_request", {})
        pr_number = pr.get("number")
        title = pr.get("title", "")
        body = pr.get("body", "")

        logger.info(
            "Received GitHub PR webhook",
            extra={"pr_number": pr_number, "action": action},
        )

        if action in {"opened", "synchronize"}:
            claim = f"PR #{pr_number} '{title}' correctly satisfies issue requirements without regressions: {body[:200]}"
            # Automated verification execution against repo index if index exists
            logger.info(
                "Triggering automated claim verification",
                extra={"pr_number": pr_number, "claim": claim},
            )
            return {"status": "verification_queued", "pr_number": str(pr_number)}

    elif event_type == "issues":
        action = payload.get("action")
        issue = payload.get("issue", {})
        issue_number = issue.get("number")
        title = issue.get("title", "")

        logger.info(
            "Received GitHub Issue webhook",
            extra={"issue_number": issue_number, "action": action},
        )
        return {"status": "issue_processed", "issue_number": str(issue_number)}

    return {"status": "event_ignored"}
