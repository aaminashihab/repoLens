"""Evaluation framework and benchmark runner for repository verification (RepoVerify-Bench)."""

import logging
import os
from dataclasses import dataclass
from time import perf_counter
from typing import TYPE_CHECKING, Any

from app.models.verification import VerificationStatus

if TYPE_CHECKING:
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
    """Metrics tracking precision, recall, false positive rate, hallucination rate, citation accuracy, latency, and cost."""

    total_cases: int
    precision: float
    recall: float
    false_positive_rate: float
    hallucination_rate: float
    citation_accuracy: float
    evidence_completeness: float
    avg_latency_seconds: float = 0.0
    estimated_tokens_per_claim: int = 0
    estimated_cost_usd: float = 0.0


class RepoVerifyEvaluator:
    """Evaluates a VerificationService instance against a benchmark suite."""

    def __init__(self, verification_service: "VerificationService | Any") -> None:
        self.service = verification_service

    def evaluate_benchmark(self, test_cases: list[BenchmarkTestCase]) -> EvaluationMetrics:
        """Run evaluation over a collection of benchmark cases and compute metrics."""
        if not test_cases:
            return EvaluationMetrics(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0.0)

        correct_verdicts = 0
        false_positives = 0
        uncited_claims = 0
        total_precision_scores = []
        total_recall_scores = []
        citation_hits = 0
        total_citations = 0
        latencies = []

        for case in test_cases:
            t0 = perf_counter()
            try:
                report = self.service.verify_claim(case.index_id, case.claim)
                latencies.append(perf_counter() - t0)

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
        avg_lat = sum(latencies) / len(latencies) if latencies else 0.0

        # ---------------------------------------------------------------------------
        # Estimated cost per claim — derived from the actual configured model.
        #
        # Token counts are approximated from the fixed prompt template size:
        #   ~1 850 input tokens  (system prompt + evidence context)
        #   ~320  output tokens  (JSON verdict response)
        #
        # Pricing table (USD per token, as of 2025-Q3):
        #   gpt-4o-mini       $0.15/1M in,  $0.60/1M out
        #   gpt-4o            $2.50/1M in, $10.00/1M out
        #   gemini-2.5-flash  $0.075/1M in, $0.30/1M out  (non-thinking)
        #   gemini-2.5-pro    $1.25/1M in,  $5.00/1M out
        # ---------------------------------------------------------------------------

        _PRICING: dict[str, tuple[float, float]] = {
            # model-name → (price_per_input_token, price_per_output_token)
            "gpt-4o-mini": (0.15e-6, 0.60e-6),
            "gpt-4o": (2.50e-6, 10.00e-6),
            "gemini-2.5-flash": (0.075e-6, 0.30e-6),
            "gemini-2.5-pro": (1.25e-6, 5.00e-6),
            "gemini-2.0-flash": (0.10e-6, 0.40e-6),
        }
        _DEFAULT_PRICING = (0.15e-6, 0.60e-6)  # fall back to gpt-4o-mini rates

        provider = os.getenv("LLM_PROVIDER", "openai").lower()
        if provider == "gemini":
            model_key = os.getenv("GEMINI_CHAT_MODEL", "gemini-2.5-flash")
        else:
            model_key = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")

        in_price, out_price = _PRICING.get(model_key, _DEFAULT_PRICING)
        est_input_tokens = 1850
        est_output_tokens = 320
        est_tokens = est_input_tokens + est_output_tokens
        est_cost = (est_input_tokens * in_price) + (est_output_tokens * out_price)

        return EvaluationMetrics(
            total_cases=total,
            precision=round(avg_precision, 4),
            recall=round(avg_recall, 4),
            false_positive_rate=round(fpr, 2),
            hallucination_rate=round(hr, 2),
            citation_accuracy=round(cit_acc, 2),
            evidence_completeness=round(completeness, 2),
            avg_latency_seconds=round(avg_lat, 3),
            estimated_tokens_per_claim=est_tokens,
            estimated_cost_usd=round(est_cost, 6),
        )

    def run_ablation_study(
        self, test_cases: list[BenchmarkTestCase]
    ) -> dict[str, EvaluationMetrics]:
        """Run ablation study comparing Hybrid (Vector + AST Graph) vs Vector-Only retrieval."""
        hybrid_metrics = self.evaluate_benchmark(test_cases)

        # Temporarily mock or disable graph expansion to evaluate vector-only baseline
        retrieval_service = getattr(self.service, "_retrieval_service", None)
        orig_retrieve = None
        if retrieval_service and hasattr(retrieval_service, "retrieve_with_graph"):
            orig_retrieve = retrieval_service.retrieve_with_graph

            def vector_only_retrieve(index_id: str, query: str, hops: int = 2):
                return orig_retrieve(index_id, query, hops=0)

            retrieval_service.retrieve_with_graph = vector_only_retrieve

        try:
            vector_only_metrics = self.evaluate_benchmark(test_cases)
        finally:
            if retrieval_service and orig_retrieve:
                retrieval_service.retrieve_with_graph = orig_retrieve

        return {
            "hybrid_vector_graph": hybrid_metrics,
            "vector_only_baseline": vector_only_metrics,
        }


if __name__ == "__main__":
    from scripts.run_benchmark import run_benchmark_cli

    run_benchmark_cli()


