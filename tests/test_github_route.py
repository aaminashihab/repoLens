import unittest
from unittest.mock import Mock
from fastapi.testclient import TestClient

import app.api.dependencies as api_deps
from app.main import app as fastapi_app

class GithubRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(fastapi_app)
        self.mock_service = Mock()
        fastapi_app.dependency_overrides[api_deps.get_verification_service] = lambda: self.mock_service
        self.orig_api_key = api_deps.API_KEY
        api_deps.API_KEY = None  # disable key check for simple testing

    def tearDown(self) -> None:
        fastapi_app.dependency_overrides.clear()
        api_deps.API_KEY = self.orig_api_key

    def test_github_webhook_pull_request_opened(self) -> None:
        headers = {"X-GitHub-Event": "pull_request"}
        payload = {
            "action": "opened",
            "pull_request": {
                "number": 42,
                "title": "Fix sql injection",
                "body": "This PR sanitizes the user input in search query"
            }
        }
        response = self.client.post("/github/webhook", json=payload, headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "verification_queued", "pr_number": "42"})

    def test_github_webhook_pull_request_ignored_action(self) -> None:
        headers = {"X-GitHub-Event": "pull_request"}
        payload = {
            "action": "closed",
            "pull_request": {
                "number": 42,
                "title": "Fix sql injection"
            }
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
                "title": "Bug in auth login"
            }
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
