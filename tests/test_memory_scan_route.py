import os
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

import app.api.dependencies as api_deps
from app.main import app as fastapi_app
from app.models.memory_scan import MemoryScanReport
from app.services.memory_scan_service import (
    MemoryScanIndexNotFoundError,
    MemoryScanServiceError,
)


class MemoryScanRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(fastapi_app)
        self.mock_service = Mock()
        fastapi_app.dependency_overrides[api_deps.get_memory_scan_service] = lambda: self.mock_service
        self._env_patcher = patch.dict("os.environ", {}, clear=False)
        self._env_patcher.start()
        os.environ.pop("API_KEY", None)

    def tearDown(self) -> None:
        fastapi_app.dependency_overrides.clear()
        self._env_patcher.stop()

    def test_scan_memory_route_success(self) -> None:
        mock_report = MemoryScanReport(
            index_id="idx-123",
            chunks_scanned=10,
            candidates_found=2,
            findings=[],
            overall_risk_score=0.0,
            summary="No issues.",
        )
        self.mock_service.scan.return_value = mock_report

        response = self.client.post("/memory-scan", json={"index_id": "idx-123"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["index_id"], "idx-123")
        self.mock_service.scan.assert_called_once_with(
            index_id="idx-123", max_findings=15, min_severity="low"
        )

    def test_scan_memory_route_404_missing_index(self) -> None:
        self.mock_service.scan.side_effect = MemoryScanIndexNotFoundError("Index 'idx-123' not found.")

        response = self.client.post("/memory-scan", json={"index_id": "idx-123"})
        self.assertEqual(response.status_code, 404)

    def test_scan_memory_route_400_service_error(self) -> None:
        self.mock_service.scan.side_effect = MemoryScanServiceError("bad request")

        response = self.client.post("/memory-scan", json={"index_id": "idx-123"})
        self.assertEqual(response.status_code, 400)

    def test_scan_memory_route_custom_params(self) -> None:
        mock_report = MemoryScanReport(
            index_id="idx-123", chunks_scanned=5, candidates_found=0, findings=[]
        )
        self.mock_service.scan.return_value = mock_report

        response = self.client.post(
            "/memory-scan",
            json={"index_id": "idx-123", "max_findings": 5, "min_severity": "high"},
        )
        self.assertEqual(response.status_code, 200)
        self.mock_service.scan.assert_called_once_with(
            index_id="idx-123", max_findings=5, min_severity="high"
        )


if __name__ == "__main__":
    unittest.main()
