import unittest
from unittest.mock import Mock

from app.core.evaluator import RepoVerifyEvaluator, BenchmarkTestCase, EvaluationMetrics
from app.models.verification import VerificationReport, VerificationStatus, EvidenceItem

class EvaluatorTests(unittest.TestCase):
    def test_evaluate_benchmark_empty(self) -> None:
        service = Mock()
        evaluator = RepoVerifyEvaluator(service)
        metrics = evaluator.evaluate_benchmark([])
        self.assertEqual(metrics.total_cases, 0)
        self.assertEqual(metrics.precision, 0.0)
        self.assertEqual(metrics.recall, 0.0)

    def test_evaluate_benchmark_metrics_calculation(self) -> None:
        service = Mock()
        
        # We will have 2 test cases:
        # Case 1: Ground truth LIKELY_TRUE. Expects evidence files "auth.py", "session.py".
        # LLM reports LIKELY_TRUE with evidence files "auth.py", "other.py".
        # Case 2: Ground truth LIKELY_FALSE. Expects evidence files "db.py".
        # LLM reports LIKELY_TRUE with no evidence. (Uncited / hallucination)
        
        case_1 = BenchmarkTestCase(
            case_id="C1",
            index_id="idx-1",
            claim="Auth is safe",
            ground_truth_status=VerificationStatus.LIKELY_TRUE,
            expected_evidence_files=["auth.py", "session.py"],
        )
        case_2 = BenchmarkTestCase(
            case_id="C2",
            index_id="idx-1",
            claim="SQL injection possible",
            ground_truth_status=VerificationStatus.LIKELY_FALSE,
            expected_evidence_files=["db.py"],
        )
        
        report_1 = VerificationReport(
            claim="Auth is safe",
            verification_status=VerificationStatus.LIKELY_TRUE,
            confidence_score=80.0,
            supporting_evidence=[
                EvidenceItem(
                    file_path="auth.py",
                    line_range="L1-L10",
                    symbol_name="login",
                    snippet="def login(): pass",
                    relevance="Check authentication logic"
                ),
                EvidenceItem(
                    file_path="other.py",
                    line_range="L5-L10",
                    symbol_name="other",
                    snippet="...",
                    relevance="..."
                )
            ]
        )
        
        # Report 2 returns LIKELY_TRUE with no evidence -> triggers false positive & hallucination (uncited)
        report_2 = VerificationReport(
            claim="SQL injection possible",
            verification_status=VerificationStatus.LIKELY_TRUE,
            confidence_score=90.0,
            supporting_evidence=[]
        )
        
        service.verify_claim.side_effect = [report_1, report_2]
        
        evaluator = RepoVerifyEvaluator(service)
        metrics = evaluator.evaluate_benchmark([case_1, case_2])
        
        # Check metrics details:
        # total_cases = 2
        # Verdict accuracy: Case 1 is correct (LIKELY_TRUE == LIKELY_TRUE). Case 2 is incorrect (LIKELY_TRUE != LIKELY_FALSE).
        # Precision calculation for Case 1: cited_files = {"auth.py", "other.py"}. expected = {"auth.py", "session.py"}.
        #   intersection = {"auth.py"}. hits = 1.
        #   precision = 1/2 = 0.5.
        #   recall = 1/2 = 0.5.
        # Case 2 has no expected_files? Actually Case 2 expected_evidence_files = ["db.py"] (expected_files = {"db.py"}).
        #   cited_files = {}. intersection = {}. hits = 0.
        #   precision = 0.0.
        #   recall = 0.0.
        #
        # avg_precision = (0.5 + 0.0) / 2 = 0.25.
        # avg_recall = (0.5 + 0.0) / 2 = 0.25.
        # false_positive_rate: 1 false positive out of 2 cases = 50.0%.
        # hallucination_rate: 1 uncited likely_true out of 2 cases = 50.0%.
        # citation_accuracy:
        #   Case 1: 2 citations. auth.py is in expected_files -> 1 hit. other.py is not in expected_files -> 0 hits.
        #   Case 2: 0 citations.
        #   total_citations = 2. citation_hits = 1.
        #   citation_accuracy = 1/2 * 100 = 50.0%.
        # evidence_completeness = avg_recall * 100 = 25.0%.
        
        self.assertEqual(metrics.total_cases, 2)
        self.assertEqual(metrics.precision, 0.25)
        self.assertEqual(metrics.recall, 0.25)
        self.assertEqual(metrics.false_positive_rate, 50.0)
        self.assertEqual(metrics.hallucination_rate, 50.0)
        self.assertEqual(metrics.citation_accuracy, 50.0)
        self.assertEqual(metrics.evidence_completeness, 25.0)

    def test_evaluate_benchmark_with_exception_in_case(self) -> None:
        service = Mock()
        service.verify_claim.side_effect = Exception("Network timeout")
        
        case = BenchmarkTestCase(
            case_id="C1",
            index_id="idx-1",
            claim="Auth is safe",
            ground_truth_status=VerificationStatus.LIKELY_TRUE,
            expected_evidence_files=[],
        )
        
        evaluator = RepoVerifyEvaluator(service)
        with self.assertLogs("app.core.evaluator", level="ERROR") as log_cm:
            metrics = evaluator.evaluate_benchmark([case])
            
        self.assertEqual(metrics.total_cases, 1)
        # Verify that errors are logged
        self.assertTrue(any("failed with error" in log for log in log_cm.output))
