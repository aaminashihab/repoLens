"""Tests for RepoVerify-Bench reproducible benchmark runner."""

import unittest

from app.core.evaluator import RepoVerifyEvaluator
from scripts.run_benchmark import build_mock_verification_service, get_default_benchmark_suite, run_benchmark_cli


class BenchmarkCliTests(unittest.TestCase):
    def test_default_benchmark_suite_has_cases(self) -> None:
        suite = get_default_benchmark_suite()
        self.assertGreaterEqual(len(suite), 10)
        for case in suite:
            self.assertTrue(case.case_id)
            self.assertTrue(case.claim)
            self.assertTrue(case.index_id)

    def test_evaluator_with_benchmark_suite(self) -> None:
        suite = get_default_benchmark_suite()
        mock_service = build_mock_verification_service()
        evaluator = RepoVerifyEvaluator(mock_service)

        metrics = evaluator.evaluate_benchmark(suite)
        self.assertEqual(metrics.total_cases, len(suite))
        self.assertGreater(metrics.precision, 0.5)
        self.assertGreater(metrics.recall, 0.5)
        self.assertLessEqual(metrics.false_positive_rate, 20.0)

    def test_run_ablation_study_returns_both_strategies(self) -> None:
        suite = get_default_benchmark_suite()
        mock_service = build_mock_verification_service()
        evaluator = RepoVerifyEvaluator(mock_service)

        ablation = evaluator.run_ablation_study(suite)
        self.assertIn("hybrid_vector_graph", ablation)
        self.assertIn("vector_only_baseline", ablation)
        self.assertEqual(ablation["hybrid_vector_graph"].total_cases, len(suite))

    def test_run_benchmark_cli_executes_without_error(self) -> None:
        # Verify CLI entrypoint prints clean benchmark table
        try:
            run_benchmark_cli()
        except Exception as exc:
            self.fail(f"run_benchmark_cli raised an unexpected exception: {exc}")
