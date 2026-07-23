"""Evaluation framework and benchmark runner for repository verification (RepoVerify-Bench)."""

import logging
from dataclasses import dataclass
from typing import Any

from app.models.verification import VerificationStatus
from app.services.verification_service import VerificationService

logger = logging.getLogger(__name__)


@dataclass
class BenchmarkTestCase:
    """A test case in the RepoVerify-Bench benchmark."""

    case_id: str
    index_id: str
    claim: str
    ground_truth_status: VerificationStatus
    expected_evidence_files: list[str]


@dataclass
class EvaluationMetrics:
    """Metrics tracking precision, recall, false positive rate, hallucination rate, and citation accuracy."""

    total_cases: int
    precision: float
    recall: float
    false_positive_rate: float
    hallucination_rate: float
    citation_accuracy: float
    evidence_completeness: float


class RepoVerifyEvaluator:
    """Evaluates a VerificationService instance against a benchmark suite."""

    def __init__(self, verification_service: VerificationService) -> None:
        self.service = verification_service

    def evaluate_benchmark(self, test_cases: list[BenchmarkTestCase]) -> EvaluationMetrics:
        """Run evaluation over a collection of benchmark cases and compute metrics."""
        if not test_cases:
            return EvaluationMetrics(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

        correct_verdicts = 0
        false_positives = 0
        uncited_claims = 0
        total_precision_scores = []
        total_recall_scores = []
        citation_hits = 0
        total_citations = 0

        for case in test_cases:
            try:
                report = self.service.verify_claim(case.index_id, case.claim)

                # Verdict Accuracy
                if report.verification_status == case.ground_truth_status:
                    correct_verdicts += 1
                elif (
                    report.verification_status == VerificationStatus.LIKELY_TRUE
                    and case.ground_truth_status == VerificationStatus.LIKELY_FALSE
                ):
                    false_positives += 1

                # Citation Accuracy
                cited_files = {item.file_path for item in report.supporting_evidence}
                expected_files = set(case.expected_evidence_files)

                if report.verification_status == VerificationStatus.LIKELY_TRUE and not cited_files:
                    uncited_claims += 1

                if expected_files:
                    hits = len(cited_files.intersection(expected_files))
                    rec = hits / len(expected_files)
                    prec = hits / len(cited_files) if cited_files else 0.0
                    total_recall_scores.append(rec)
                    total_precision_scores.append(prec)

                for item in report.supporting_evidence:
                    total_citations += 1
                    if not expected_files or item.file_path in expected_files:
                        citation_hits += 1

            except Exception as exc:
                logger.error(
                    "Benchmark case %s failed with error: %s",
                    case.case_id,
                    exc,
                )

        total = len(test_cases)
        avg_precision = (
            sum(total_precision_scores) / len(total_precision_scores)
            if total_precision_scores
            else 1.0
        )
        avg_recall = (
            sum(total_recall_scores) / len(total_recall_scores)
            if total_recall_scores
            else 1.0
        )
        fpr = (false_positives / total) * 100.0
        hr = (uncited_claims / total) * 100.0
        cit_acc = (citation_hits / total_citations * 100.0) if total_citations > 0 else 100.0
        completeness = avg_recall * 100.0

        return EvaluationMetrics(
            total_cases=total,
            precision=round(avg_precision, 4),
            recall=round(avg_recall, 4),
            false_positive_rate=round(fpr, 2),
            hallucination_rate=round(hr, 2),
            citation_accuracy=round(cit_acc, 2),
            evidence_completeness=round(completeness, 2),
        )
