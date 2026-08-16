import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from app.models.verification import VerificationStatus
from app.services.retrieval_service import (
    IndexNotFoundError,
    RetrievalServiceError,
    RetrievedChunk,
)
from app.services.verification_service import (
    VerificationService,
    VerificationServiceError,
)


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
                    "status": "VERIFIED",
                }
            ],
            "supporting_evidence": [
                {
                    "file_path": "auth.py",
                    "line_range": "L1-L2",
                    "symbol_name": "check_auth",
                    "snippet": "def check_auth(user): return user.is_admin",
                    "relevance": "Verifies admin checks user.is_admin",
                }
            ],
            "contradicting_evidence": [],
            "potential_risks": [],
            "missing_information": [],
            "recommended_tests": [],
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
            model="gpt-4o-mini",
        )
        service._provider = "openai"

        report = service.verify_claim("index-1", "Auth checks admin flag")

        self.assertEqual(report.claim, "Auth checks admin flag")
        self.assertEqual(
            report.verification_status,
            VerificationStatus.LIKELY_TRUE,
        )
        self.assertEqual(report.confidence_score, 90.0)
        self.assertEqual(len(report.supporting_evidence), 1)
        self.assertEqual(
            report.supporting_evidence[0].file_path,
            "auth.py",
        )

        self.retrieval_service.retrieve_with_graph.assert_called_once_with(
            "index-1",
            "Auth checks admin flag",
            hops=2,
        )

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
                    "relevance": "Verifies admin checks user.is_admin",
                }
            ],
            "contradicting_evidence": [],
            "potential_risks": [],
            "missing_information": [],
            "recommended_tests": [],
        }

        self.client.models.generate_content.return_value = SimpleNamespace(
            text=f"```json\n{json.dumps(mock_json_response)}\n```"
        )

        service = VerificationService(
            retrieval_service=self.retrieval_service,
            client=self.client,
            model="gemini-2.5-flash",
        )
        service._provider = "gemini"

        report = service.verify_claim("index-1", "Auth checks admin flag")

        self.assertEqual(
            report.verification_status,
            VerificationStatus.LIKELY_TRUE,
        )
        self.assertEqual(report.confidence_score, 85.0)

    def test_verify_claim_index_not_found(self) -> None:
        self.retrieval_service.retrieve_with_graph.side_effect = (
            IndexNotFoundError("Index 'index-missing' not found.")
        )

        service = VerificationService(
            retrieval_service=self.retrieval_service,
            client=self.client,
        )

        with self.assertRaises(IndexNotFoundError):
            service.verify_claim("index-missing", "Some claim")

    def test_verify_claim_retrieval_error(self) -> None:
        self.retrieval_service.retrieve_with_graph.side_effect = (
            RetrievalServiceError("Database error")
        )

        service = VerificationService(
            retrieval_service=self.retrieval_service,
            client=self.client,
        )

        with self.assertRaises(VerificationServiceError):
            service.verify_claim("index-1", "Some claim")

    def test_verify_claim_empty_retrieval(self) -> None:
        self.retrieval_service.retrieve_with_graph.return_value = []

        service = VerificationService(
            retrieval_service=self.retrieval_service,
            client=self.client,
        )

        report = service.verify_claim("index-1", "Some claim")

        self.assertEqual(
            report.verification_status,
            VerificationStatus.UNCERTAIN,
        )
        self.assertEqual(report.confidence_score, 0.0)
        self.assertIn(
            "No code chunks found in index.",
            report.potential_risks,
        )

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
        self.client.chat.completions.create.side_effect = Exception(
            "API rate limit exceeded"
        )

        service = VerificationService(
            retrieval_service=self.retrieval_service,
            client=self.client,
        )
        service._provider = "openai"

        report = service.verify_claim("index-1", "Some claim")

        self.assertEqual(
            report.verification_status,
            VerificationStatus.UNCERTAIN,
        )
        self.assertEqual(report.confidence_score, 0.0)
        self.assertTrue(
            any(
                "API rate limit exceeded" in info
                for info in report.missing_information
            )
        )

    def test_verify_claim_supported_claim_has_supporting_evidence(self) -> None:
        chunk = RetrievedChunk(
            text="def is_admin(user): return user.role == 'admin'",
            file_path="auth.py",
            symbol_name="is_admin",
            chunk_type="function",
            similarity_score=0.95,
            start_line=1,
            end_line=1,
        )
        self.retrieval_service.retrieve_with_graph.return_value = [chunk] * 5

        mock_json_response = {
            "verification_status": "Likely True",
            "confidence_score": 95.0,
            "atomic_hypotheses": [
                {
                    "hypothesis_id": "H1",
                    "statement": (
                        "The function checks whether the user has "
                        "the admin role."
                    ),
                    "status": "VERIFIED",
                }
            ],
            "supporting_evidence": [
                {
                    "file_path": "auth.py",
                    "line_range": "L1-L1",
                    "symbol_name": "is_admin",
                    "snippet": "return user.role == 'admin'",
                    "relevance": (
                        "The function directly checks the user's role."
                    ),
                }
            ],
            "contradicting_evidence": [],
            "potential_risks": [],
            "missing_information": [],
            "recommended_tests": [],
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
            model="gpt-4o-mini",
        )
        service._provider = "openai"

        report = service.verify_claim(
            "index-supported",
            "The is_admin function checks whether the user has "
            "the admin role",
        )

        self.assertEqual(
            report.verification_status,
            VerificationStatus.LIKELY_TRUE,
        )
        self.assertEqual(report.confidence_score, 95.0)
        self.assertEqual(len(report.supporting_evidence), 1)
        self.assertEqual(
            report.supporting_evidence[0].file_path,
            "auth.py",
        )
        self.assertFalse(
            report.supporting_evidence[0].is_contradictory,
        )

    def test_verify_claim_contradicted_claim_has_contradicting_evidence(
        self,
    ) -> None:
        chunk = RetrievedChunk(
            text=(
                "def delete_user(user): "
                "return database.delete(user.id)"
            ),
            file_path="users.py",
            symbol_name="delete_user",
            chunk_type="function",
            similarity_score=0.95,
            start_line=1,
            end_line=1,
        )
        self.retrieval_service.retrieve_with_graph.return_value = [chunk] * 5

        mock_json_response = {
            "verification_status": "Likely False",
            "confidence_score": 92.0,
            "atomic_hypotheses": [
                {
                    "hypothesis_id": "H1",
                    "statement": (
                        "The function retries failed database deletions."
                    ),
                    "status": "REFUTED",
                }
            ],
            "supporting_evidence": [],
            "contradicting_evidence": [
                {
                    "file_path": "users.py",
                    "line_range": "L1-L1",
                    "symbol_name": "delete_user",
                    "snippet": (
                        "return database.delete(user.id)"
                    ),
                    "relevance": (
                        "The function performs one deletion call "
                        "and contains no retry logic."
                    ),
                }
            ],
            "potential_risks": [],
            "missing_information": [],
            "recommended_tests": [],
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
            model="gpt-4o-mini",
        )
        service._provider = "openai"

        report = service.verify_claim(
            "index-contradicted",
            "The delete_user function retries failed "
            "database deletions",
        )

        self.assertEqual(
            report.verification_status,
            VerificationStatus.LIKELY_FALSE,
        )
        self.assertEqual(report.confidence_score, 92.0)
        self.assertEqual(len(report.contradicting_evidence), 1)
        self.assertEqual(
            report.contradicting_evidence[0].file_path,
            "users.py",
        )
        self.assertTrue(
            report.contradicting_evidence[0].is_contradictory,
        )

    def test_verify_claim_insufficient_evidence_returns_uncertain(
        self,
    ) -> None:
        chunk = RetrievedChunk(
            text="def process_data(data): return transform(data)",
            file_path="processor.py",
            symbol_name="process_data",
            chunk_type="function",
            similarity_score=0.95,
            start_line=1,
            end_line=1,
        )
        self.retrieval_service.retrieve_with_graph.return_value = [chunk] * 5

        mock_json_response = {
            "verification_status": "Uncertain",
            "confidence_score": 35.0,
            "atomic_hypotheses": [
                {
                    "hypothesis_id": "H1",
                    "statement": (
                        "The function stores processed data in PostgreSQL."
                    ),
                    "status": "UNVERIFIABLE",
                }
            ],
            "supporting_evidence": [],
            "contradicting_evidence": [],
            "potential_risks": [],
            "missing_information": [
                "No evidence shows that PostgreSQL is used by this function."
            ],
            "recommended_tests": [],
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
            model="gpt-4o-mini",
        )
        service._provider = "openai"

        report = service.verify_claim(
            "index-uncertain",
            "The process_data function stores processed data in PostgreSQL",
        )

        self.assertEqual(
            report.verification_status,
            VerificationStatus.UNCERTAIN,
        )
        self.assertEqual(report.confidence_score, 35.0)
        self.assertEqual(report.supporting_evidence, [])
        self.assertEqual(report.contradicting_evidence, [])
        self.assertTrue(report.missing_information)


if __name__ == "__main__":
    unittest.main()