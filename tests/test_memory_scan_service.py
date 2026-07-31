import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from app.models.memory_scan import MemoryVerdict
from app.services.chunk_service import CodeChunk
from app.services.index_service import IndexServiceError
from app.services.memory_scan_service import (
    MemoryScanIndexNotFoundError,
    MemoryScanService,
)


def _make_chunk(content: str, file_path: str = "worker.py", symbol_name: str = "collect") -> CodeChunk:
    return CodeChunk(
        chunk_id="c1",
        file_path=file_path,
        language="python",
        symbol_name=symbol_name,
        symbol_type="function",
        parent_symbol=None,
        start_line=1,
        end_line=content.count("\n") + 1,
        content=content,
    )


class MemoryScanServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.index_service = Mock()
        self.client = Mock()

    def test_scan_success_openai(self) -> None:
        chunk = _make_chunk(
            "def collect(rows):\n"
            "    results = []\n"
            "    for row in rows:\n"
            "        results.append(process(row))\n"
            "    return results\n"
        )
        self.index_service.load_index.return_value = SimpleNamespace(chunks=[chunk])

        mock_json_response = {
            "findings": [
                {
                    "id": "C1",
                    "verdict": "Confirmed",
                    "confidence_score": 88.0,
                    "explanation": "Unbounded list grows with input size.",
                    "suggested_fix": "Stream results or cap the buffer size.",
                }
            ]
        }
        self.client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(mock_json_response)))]
        )

        service = MemoryScanService(index_service=self.index_service, client=self.client, model="gpt-4o-mini")
        service._provider = "openai"

        report = service.scan("index-1")

        self.assertEqual(report.index_id, "index-1")
        self.assertEqual(report.chunks_scanned, 1)
        self.assertGreaterEqual(report.candidates_found, 1)
        self.assertEqual(len(report.findings), 1)
        self.assertEqual(report.findings[0].verdict, MemoryVerdict.CONFIRMED)
        self.assertEqual(report.findings[0].file_path, "worker.py")
        self.assertGreater(report.overall_risk_score, 0.0)

    def test_scan_success_gemini(self) -> None:
        chunk = _make_chunk("_CACHE = {}\n\ndef get(key):\n    return _CACHE[key]\n")
        self.index_service.load_index.return_value = SimpleNamespace(chunks=[chunk])

        mock_json_response = {
            "findings": [
                {
                    "id": "C1",
                    "verdict": "Likely",
                    "confidence_score": 70.0,
                    "explanation": "Cache has no eviction policy.",
                    "suggested_fix": "Add a max size or TTL.",
                }
            ]
        }
        self.client.models.generate_content.return_value = SimpleNamespace(
            text=f"```json\n{json.dumps(mock_json_response)}\n```"
        )

        service = MemoryScanService(index_service=self.index_service, client=self.client, model="gemini-2.5-flash")
        service._provider = "gemini"

        report = service.scan("index-1")
        self.assertEqual(len(report.findings), 1)
        self.assertEqual(report.findings[0].verdict, MemoryVerdict.LIKELY)

    def test_scan_index_not_found(self) -> None:
        self.index_service.load_index.side_effect = IndexServiceError("Index 'missing' does not exist.")

        service = MemoryScanService(index_service=self.index_service, client=self.client)

        with self.assertRaises(MemoryScanIndexNotFoundError):
            service.scan("missing")

    def test_scan_no_candidates(self) -> None:
        chunk = _make_chunk("def add(a, b):\n    return a + b\n")
        self.index_service.load_index.return_value = SimpleNamespace(chunks=[chunk])

        service = MemoryScanService(index_service=self.index_service, client=self.client)

        report = service.scan("index-1")
        self.assertEqual(report.candidates_found, 0)
        self.assertEqual(report.findings, [])
        self.assertEqual(report.overall_risk_score, 0.0)
        # LLM should never be called when there are no heuristic candidates.
        self.client.chat.completions.create.assert_not_called()

    def test_scan_llm_failure_fallback(self) -> None:
        chunk = _make_chunk(
            "def collect(rows):\n"
            "    results = []\n"
            "    for row in rows:\n"
            "        results.append(process(row))\n"
            "    return results\n"
        )
        self.index_service.load_index.return_value = SimpleNamespace(chunks=[chunk])
        self.client.chat.completions.create.side_effect = Exception("rate limited")

        service = MemoryScanService(index_service=self.index_service, client=self.client)
        service._provider = "openai"

        report = service.scan("index-1")
        self.assertEqual(len(report.findings), 1)
        self.assertEqual(report.findings[0].verdict, MemoryVerdict.UNCERTAIN)

    def test_scan_min_severity_filter(self) -> None:
        chunk = _make_chunk("df = pd.read_csv('huge.csv')\n")  # medium severity
        self.index_service.load_index.return_value = SimpleNamespace(chunks=[chunk])

        service = MemoryScanService(index_service=self.index_service, client=self.client)
        report = service.scan("index-1", min_severity="high")
        self.assertEqual(report.candidates_found, 0)
        self.client.chat.completions.create.assert_not_called()

    def test_scan_guardrail_strips_unknown_file_path(self) -> None:
        chunk = _make_chunk(
            "def collect(rows):\n"
            "    results = []\n"
            "    for row in rows:\n"
            "        results.append(process(row))\n"
            "    return results\n"
        )
        self.index_service.load_index.return_value = SimpleNamespace(chunks=[chunk])

        mock_json_response = {
            "findings": [
                {
                    "id": "C1",
                    "verdict": "Confirmed",
                    "confidence_score": 90.0,
                    "explanation": "Looks unbounded.",
                    "suggested_fix": None,
                }
            ]
        }
        self.client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(mock_json_response)))]
        )

        service = MemoryScanService(index_service=self.index_service, client=self.client)
        service._provider = "openai"
        report = service.scan("index-1")

        # file_path came straight from the indexed chunk, so it must survive the guardrail.
        self.assertEqual(len(report.findings), 1)
        self.assertEqual(report.findings[0].file_path, "worker.py")


if __name__ == "__main__":
    unittest.main()
