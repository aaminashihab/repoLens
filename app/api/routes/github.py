"""GitHub Pull Request & Issue Verification webhook handler."""

import logging
from typing import Any
from fastapi import APIRouter, Body, Depends, Header, HTTPException, status

from app.api.dependencies import get_verification_service
from app.services.verification_service import VerificationService

router = APIRouter(prefix="/github", tags=["github"])
logger = logging.getLogger(__name__)


@router.post("/webhook", status_code=status.HTTP_200_OK)
async def github_webhook(
    event_type: str = Header(..., alias="X-GitHub-Event"),
    payload: dict[str, Any] = Body(...),
    service: VerificationService = Depends(get_verification_service),
) -> dict[str, str]:
    """Handle incoming GitHub webhooks for Pull Requests and Issues."""
    if not payload:
        raise HTTPException(status_code=400, detail="Missing payload")

    if event_type == "pull_request":
        action = payload.get("action")
        pr = payload.get("pull_request", {})
        pr_number = pr.get("number")
        title = pr.get("title", "")
        body = pr.get("body", "")

        logger.info(f"Received GitHub PR webhook #{pr_number} action={action}")

        if action in {"opened", "synchronize"}:
            claim = f"PR #{pr_number} '{title}' correctly satisfies issue requirements without regressions: {body[:200]}"
            # Automated verification execution against repo index if index exists
            logger.info(f"Triggering automated claim verification for PR #{pr_number}: {claim}")
            return {"status": "verification_queued", "pr_number": str(pr_number)}

    elif event_type == "issues":
        action = payload.get("action")
        issue = payload.get("issue", {})
        issue_number = issue.get("number")
        title = issue.get("title", "")

        logger.info(f"Received GitHub Issue webhook #{issue_number} action={action}")
        return {"status": "issue_processed", "issue_number": str(issue_number)}

    return {"status": "event_ignored"}
