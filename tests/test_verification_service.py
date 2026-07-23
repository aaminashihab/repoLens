import os
import unittest
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.services.verification_service import VerificationService, VerificationServiceError
from app.services.retrieval_service import IndexNotFoundError, RetrievalServiceError, RetrievedChunk
from app.models.verification import VerificationStatus

class VerificationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.retrieval_service = Mock()
        self.client = Mock()

    def test_verify_claim_success_openai(self) -> None:
        chunk = RetrievedChunk(
            text="def check_auth(user): return user.is_admin",
            file_path="auth.py",
            symbol_name="check_auth",
            chunk_type="function",
            similarity_score=0.9,
            start_line=1,
            end_line=2,
        )
        self.retrieval_service.retrieve_with_graph.return_value = [chunk] * 5

        mock_json_response = {
            "verification_status": "Likely True",
            "confidence_score": 90.0,
            "atomic_hypotheses": [
                {
                    "hypothesis_id": "H1",
                    "statement": "Admin check is present",
                    "status": "VERIFIED"
                }
            ],
            "supporting_evidence": [
                {
                    "file_path": "auth.py",
                    "line_range": "L1-L2",
                    "symbol_name": "check_auth",
                    "snippet": "def check_auth(user): return user.is_admin",
                    "relevance": "Verifies admin checks user.is_admin"
                }
            ],
            "contradicting_evidence": [],
            "potential_risks": [],
            "missing_information": [],
            "recommended_tests": []
        }

        self.client.chat.completions.create.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps(mock_json_response)
                    )
                )
            ]
        )

        service = VerificationService(
            retrieval_service=self.retrieval_service,
            client=self.client,
            model="gpt-4o-mini"
        )
        service._provider = "openai"

        report = service.verify_claim("index-1", "Auth checks admin flag")

        self.assertEqual(report.claim, "Auth checks admin flag")
        self.assertEqual(report.verification_status, VerificationStatus.LIKELY_TRUE)
        self.assertEqual(report.confidence_score, 90.0)
        self.assertEqual(len(report.supporting_evidence), 1)
        self.assertEqual(report.supporting_evidence[0].file_path, "auth.py")

        self.retrieval_service.retrieve_with_graph.assert_called_once_with("index-1", "Auth checks admin flag", hops=2)

    def test_verify_claim_success_gemini(self) -> None:
        chunk = RetrievedChunk(
            text="def check_auth(user): return user.is_admin",
            file_path="auth.py",
            symbol_name="check_auth",
            chunk_type="function",
            similarity_score=0.9,
            start_line=1,
            end_line=2,
        )
        self.retrieval_service.retrieve_with_graph.return_value = [chunk] * 5

        mock_json_response = {
            "verification_status": "Likely True",
            "confidence_score": 85.0,
            "atomic_hypotheses": [],
            "supporting_evidence": [
                {
                    "file_path": "auth.py",
                    "line_range": "L1-L2",
                    "symbol_name": "check_auth",
                    "snippet": "def check_auth(user): return user.is_admin",
                    "relevance": "Verifies admin checks user.is_admin"
                }
            ],
            "contradicting_evidence": [],
            "potential_risks": [],
            "missing_information": [],
            "recommended_tests": []
        }

        self.client.models.generate_content.return_value = SimpleNamespace(
            text=f"```json\n{json.dumps(mock_json_response)}\n```"
        )

        service = VerificationService(
            retrieval_service=self.retrieval_service,
            client=self.client,
            model="gemini-2.5-flash"
        )
        service._provider = "gemini"

        report = service.verify_claim("index-1", "Auth checks admin flag")
        self.assertEqual(report.verification_status, VerificationStatus.LIKELY_TRUE)
        self.assertEqual(report.confidence_score, 85.0)

    def test_verify_claim_index_not_found(self) -> None:
        self.retrieval_service.retrieve_with_graph.side_effect = IndexNotFoundError("Index 'index-missing' not found.")

        service = VerificationService(
            retrieval_service=self.retrieval_service,
            client=self.client
        )

        with self.assertRaises(IndexNotFoundError):
            service.verify_claim("index-missing", "Some claim")

    def test_verify_claim_retrieval_error(self) -> None:
        self.retrieval_service.retrieve_with_graph.side_effect = RetrievalServiceError("Database error")

        service = VerificationService(
            retrieval_service=self.retrieval_service,
            client=self.client
        )

        with self.assertRaises(VerificationServiceError):
            service.verify_claim("index-1", "Some claim")

    def test_verify_claim_empty_retrieval(self) -> None:
        self.retrieval_service.retrieve_with_graph.return_value = []

        service = VerificationService(
            retrieval_service=self.retrieval_service,
            client=self.client
        )

        report = service.verify_claim("index-1", "Some claim")
        self.assertEqual(report.verification_status, VerificationStatus.UNCERTAIN)
        self.assertEqual(report.confidence_score, 0.0)
        self.assertIn("No code chunks found in index.", report.potential_risks)

    def test_verify_claim_llm_failure_fallback(self) -> None:
        chunk = RetrievedChunk(
            text="def check_auth(user): return user.is_admin",
            file_path="auth.py",
            symbol_name="check_auth",
            chunk_type="function",
            similarity_score=0.9,
            start_line=1,
            end_line=2,
        )
        self.retrieval_service.retrieve_with_graph.return_value = [chunk]
        self.client.chat.completions.create.side_effect = Exception("API rate limit exceeded")

        service = VerificationService(
            retrieval_service=self.retrieval_service,
            client=self.client
        )
        service._provider = "openai"

        report = service.verify_claim("index-1", "Some claim")
        self.assertEqual(report.verification_status, VerificationStatus.UNCERTAIN)
        self.assertEqual(report.confidence_score, 0.0)
        self.assertTrue(any("API rate limit exceeded" in info for info in report.missing_information))
