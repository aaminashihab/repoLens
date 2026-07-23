"""GitHub Pull Request & Issue Verification webhook handler."""

import hashlib
import hmac
import logging
import os
from typing import Any
from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request, status

from app.api.dependencies import get_index_service, get_verification_service, limiter
from app.services.index_service import IndexService
from app.services.verification_service import VerificationService

GITHUB_RATE_LIMIT = os.getenv("GITHUB_RATE_LIMIT", "30/minute")


def get_github_rate_limit() -> str:
    return GITHUB_RATE_LIMIT


router = APIRouter(prefix="/github", tags=["github"])
logger = logging.getLogger(__name__)


def _find_index_for_repo(repo_url: str, index_service: IndexService) -> str | None:
    """Find the most recent non-empty index ID matching the target GitHub repository URL."""
    if not repo_url:
        return None
    normalized_target = repo_url.rstrip("/").removesuffix(".git").lower()
    indexes = index_service.list_indexes()
    for idx in sorted(indexes, key=lambda x: x.get("created_at", ""), reverse=True):
        idx_url = idx.get("repo_url", "").rstrip("/").removesuffix(".git").lower()
        if idx_url == normalized_target and idx.get("vector_count", 0) > 0:
            return idx["index_id"]
    return None


@router.post("/webhook", status_code=status.HTTP_200_OK)
@limiter.limit(get_github_rate_limit)
async def github_webhook(
    request: Request,
    event_type: str = Header(..., alias="X-GitHub-Event"),
    x_hub_signature_256: str | None = Header(None, alias="X-Hub-Signature-256"),
    payload: dict[str, Any] = Body(...),
    service: VerificationService = Depends(get_verification_service),
    index_service: IndexService = Depends(get_index_service),
) -> dict[str, Any]:
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

    repo_data = payload.get("repository", {})
    repo_url = repo_data.get("html_url") or repo_data.get("clone_url") or ""

    if event_type == "pull_request":
        action = payload.get("action")
        pr = payload.get("pull_request", {})
        pr_number = pr.get("number")
        title = pr.get("title", "")
        body = pr.get("body", "") or ""

        logger.info(
            "Received GitHub PR webhook",
            extra={"pr_number": pr_number, "action": action, "repo_url": repo_url},
        )

        if action in {"opened", "synchronize"}:
            target_index = _find_index_for_repo(repo_url, index_service)
            claim = f"PR #{pr_number} '{title}' satisfies requirements without regressions: {body[:200]}"

            if not target_index:
                logger.info(
                    "No active index found for repository; skipping verification",
                    extra={"pr_number": pr_number, "repo_url": repo_url},
                )
                return {
                    "status": "verification_skipped",
                    "pr_number": str(pr_number),
                    "reason": "No active index found for repository. Index repository first via /index-repository.",
                }

            logger.info(
                "Executing automated claim verification for PR",
                extra={"pr_number": pr_number, "index_id": target_index, "claim": claim},
            )
            report = service.verify_claim(
                index_id=target_index,
                claim=claim,
                repository_url=repo_url,
                pr_number=pr_number,
            )
            return {
                "status": "verification_completed",
                "pr_number": str(pr_number),
                "index_id": target_index,
                "verification_status": report.verification_status.value,
                "confidence_score": report.confidence_score,
                "supporting_evidence_count": len(report.supporting_evidence),
            }

    elif event_type == "issues":
        action = payload.get("action")
        issue = payload.get("issue", {})
        issue_number = issue.get("number")
        title = issue.get("title", "")
        body = issue.get("body", "") or ""

        logger.info(
            "Received GitHub Issue webhook",
            extra={"issue_number": issue_number, "action": action, "repo_url": repo_url},
        )

        if action in {"opened"}:
            target_index = _find_index_for_repo(repo_url, index_service)
            if target_index:
                claim = f"Issue #{issue_number} '{title}' describes a bug or behavior present in the codebase: {body[:200]}"
                report = service.verify_claim(
                    index_id=target_index,
                    claim=claim,
                    repository_url=repo_url,
                    issue_number=issue_number,
                )
                return {
                    "status": "issue_verification_completed",
                    "issue_number": str(issue_number),
                    "index_id": target_index,
                    "verification_status": report.verification_status.value,
                    "confidence_score": report.confidence_score,
                }

        return {"status": "issue_processed", "issue_number": str(issue_number)}

    return {"status": "event_ignored"}
