import hashlib
import hmac
import json
import os
import unittest
from unittest.mock import Mock, patch
from fastapi.testclient import TestClient

import app.api.dependencies as api_deps
from app.main import app as fastapi_app


def compute_signature(secret: str, body_bytes: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()


class GithubRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        import app.api.routes.github as api_github
        api_github.GITHUB_RATE_LIMIT = "30/minute"
        fastapi_app.state.limiter.reset()
        self.client = TestClient(fastapi_app)
        self.mock_service = Mock()
        fastapi_app.dependency_overrides[api_deps.get_verification_service] = lambda: self.mock_service
        self.orig_api_key = api_deps.API_KEY
        api_deps.API_KEY = None  # disable key check for simple testing

    def tearDown(self) -> None:
        import app.api.routes.github as api_github
        api_github.GITHUB_RATE_LIMIT = "30/minute"
        fastapi_app.state.limiter.reset()
        fastapi_app.dependency_overrides.clear()
        api_deps.API_KEY = self.orig_api_key

    def test_github_webhook_pull_request_opened_no_index(self) -> None:
        headers = {"X-GitHub-Event": "pull_request"}
        payload = {
            "action": "opened",
            "pull_request": {
                "number": 42,
                "title": "Fix sql injection",
                "body": "This PR sanitizes the user input in search query",
            },
            "repository": {
                "clone_url": "https://github.com/owner/repo.git",
            },
        }
        response = self.client.post("/github/webhook", json=payload, headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "verification_skipped")
        self.assertEqual(response.json()["pr_number"], "42")

    def test_github_webhook_pull_request_opened_with_matching_index(self) -> None:
        mock_index_service = Mock()
        mock_index_service.list_indexes.return_value = [
            {"index_id": "idx-123", "repo_url": "https://github.com/owner/repo.git", "vector_count": 50, "created_at": "2026-07-23T00:00:00Z"}
        ]
        fastapi_app.dependency_overrides[api_deps.get_index_service] = lambda: mock_index_service

        from app.models.verification import VerificationReport, VerificationStatus
        mock_report = Mock(spec=VerificationReport)
        mock_report.verification_status = VerificationStatus.LIKELY_TRUE
        mock_report.confidence_score = 95.0
        mock_report.supporting_evidence = []
        self.mock_service.verify_claim.return_value = mock_report

        headers = {"X-GitHub-Event": "pull_request"}
        payload = {
            "action": "opened",
            "pull_request": {
                "number": 42,
                "title": "Fix sql injection",
                "body": "This PR sanitizes user input",
            },
            "repository": {
                "clone_url": "https://github.com/owner/repo.git",
            },
        }
        response = self.client.post("/github/webhook", json=payload, headers=headers)
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        self.assertEqual(res_data["status"], "verification_completed")
        self.assertEqual(res_data["pr_number"], "42")
        self.assertEqual(res_data["verification_status"], "Likely True")
        self.assertEqual(res_data["confidence_score"], 95.0)

    def test_github_webhook_pull_request_ignored_action(self) -> None:
        headers = {"X-GitHub-Event": "pull_request"}
        payload = {
            "action": "closed",
            "pull_request": {
                "number": 42,
                "title": "Fix sql injection",
            },
        }
        response = self.client.post("/github/webhook", json=payload, headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "event_ignored"})

    def test_github_webhook_issues(self) -> None:
        headers = {"X-GitHub-Event": "issues"}
        payload = {
            "action": "opened",
            "issue": {
                "number": 101,
                "title": "Bug in auth login",
            },
        }
        response = self.client.post("/github/webhook", json=payload, headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "issue_processed", "issue_number": "101"})

    def test_github_webhook_unhandled_event(self) -> None:
        headers = {"X-GitHub-Event": "star"}
        payload = {"action": "created"}
        response = self.client.post("/github/webhook", json=payload, headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "event_ignored"})

    def test_github_webhook_missing_event_header(self) -> None:
        payload = {"action": "opened"}
        response = self.client.post("/github/webhook", json=payload)
        # Should fail with 422 Unprocessable Entity because of missing header
        self.assertEqual(response.status_code, 422)

    @patch.dict(os.environ, {"GITHUB_WEBHOOK_SECRET": "my_webhook_secret"})
    def test_github_webhook_valid_signature_accepted(self) -> None:
        payload = {"action": "opened", "issue": {"number": 10, "title": "Test Issue"}}
        body_bytes = json.dumps(payload).encode("utf-8")
        sig = compute_signature("my_webhook_secret", body_bytes)
        headers = {
            "X-GitHub-Event": "issues",
            "X-Hub-Signature-256": sig,
            "Content-Type": "application/json",
        }
        response = self.client.post("/github/webhook", content=body_bytes, headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "issue_processed", "issue_number": "10"})

    @patch.dict(os.environ, {"GITHUB_WEBHOOK_SECRET": "my_webhook_secret"})
    def test_github_webhook_missing_signature_rejected(self) -> None:
        payload = {"action": "opened", "issue": {"number": 10, "title": "Test Issue"}}
        headers = {"X-GitHub-Event": "issues", "Content-Type": "application/json"}
        response = self.client.post("/github/webhook", json=payload, headers=headers)
        self.assertEqual(response.status_code, 401)
        self.assertIn("Invalid or missing X-Hub-Signature-256 header", response.json()["detail"])

    @patch.dict(os.environ, {"GITHUB_WEBHOOK_SECRET": "my_webhook_secret"})
    def test_github_webhook_invalid_signature_rejected(self) -> None:
        payload = {"action": "opened", "issue": {"number": 10, "title": "Test Issue"}}
        body_bytes = json.dumps(payload).encode("utf-8")
        headers = {
            "X-GitHub-Event": "issues",
            "X-Hub-Signature-256": "sha256=0000000000000000000000000000000000000000000000000000000000000000",
            "Content-Type": "application/json",
        }
        response = self.client.post("/github/webhook", content=body_bytes, headers=headers)
        self.assertEqual(response.status_code, 401)
        self.assertIn("signature verification failed", response.json()["detail"])

    def test_github_webhook_rate_limiting(self) -> None:
        import app.api.routes.github as api_github
        api_github.GITHUB_RATE_LIMIT = "1/minute"
        fastapi_app.state.limiter.reset()

        headers = {"X-GitHub-Event": "issues"}
        payload = {"action": "opened", "issue": {"number": 1, "title": "First"}}

        res1 = self.client.post("/github/webhook", json=payload, headers=headers)
        self.assertEqual(res1.status_code, 200)

        res2 = self.client.post("/github/webhook", json=payload, headers=headers)
        self.assertEqual(res2.status_code, 429)

