import unittest
from unittest.mock import Mock, patch
from fastapi.testclient import TestClient

import app.api.dependencies as api_deps
from app.main import app as fastapi_app
from app.services.verification_service import VerificationServiceError
from app.services.retrieval_service import IndexNotFoundError
from app.models.verification import VerificationReport, VerificationStatus

class VerifyRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(fastapi_app)
        self.mock_service = Mock()
        
        # Override dependency
        fastapi_app.dependency_overrides[api_deps.get_verification_service] = lambda: self.mock_service
        self.orig_api_key = api_deps.API_KEY
        api_deps.API_KEY = None  # disable key check for simple testing

    def tearDown(self) -> None:
        fastapi_app.dependency_overrides.clear()
        api_deps.API_KEY = self.orig_api_key

    def test_verify_claim_route_success(self) -> None:
        mock_report = VerificationReport(
            claim="Middleware checks admin",
            verification_status=VerificationStatus.LIKELY_TRUE,
            confidence_score=95.0,
            supporting_evidence=[]
        )
        self.mock_service.verify_claim.return_value = mock_report

        payload = {
            "index_id": "idx-123",
            "claim": "Middleware checks admin",
            "repository_url": "https://github.com/user/repo"
        }

        response = self.client.post("/verify", json=payload)
        self.assertEqual(response.status_code, 200)
        resp_data = response.json()
        self.assertEqual(resp_data["claim"], "Middleware checks admin")
        self.assertEqual(resp_data["verification_status"], "Likely True")
        self.assertEqual(resp_data["confidence_score"], 95.0)
        self.mock_service.verify_claim.assert_called_once_with(
            index_id="idx-123",
            claim="Middleware checks admin",
            repository_url="https://github.com/user/repo",
            pr_number=None,
            issue_number=None
        )

    def test_verify_claim_route_404_missing_index(self) -> None:
        self.mock_service.verify_claim.side_effect = IndexNotFoundError("Index idx-123 was not found.")

        payload = {
            "index_id": "idx-123",
            "claim": "Middleware checks admin"
        }

        response = self.client.post("/verify", json=payload)
        self.assertEqual(response.status_code, 404)
        self.assertIn("idx-123 was not found", response.json()["detail"])

    def test_verify_claim_route_400_service_error(self) -> None:
        self.mock_service.verify_claim.side_effect = VerificationServiceError("LLM judge failed")

        payload = {
            "index_id": "idx-123",
            "claim": "Middleware checks admin"
        }

        response = self.client.post("/verify", json=payload)
        self.assertEqual(response.status_code, 400)
        self.assertIn("LLM judge failed", response.json()["detail"])

    def test_verify_claim_route_500_unexpected_error(self) -> None:
        self.mock_service.verify_claim.side_effect = Exception("Out of memory")

        payload = {
            "index_id": "idx-123",
            "claim": "Middleware checks admin"
        }

        with patch("app.api.routes.verify.logger.exception") as mock_log:
            response = self.client.post("/verify", json=payload)
            self.assertEqual(response.status_code, 500)
            self.assertIn("internal server error occurred", response.json()["detail"])
            mock_log.assert_called_once()
