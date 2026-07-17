import tempfile
import unittest
from pathlib import Path
import shutil

from fastapi import HTTPException
from app.services.job_service import JobService
from app.api.routes.repositories import get_indexing_status

class JobServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.service = JobService(storage_path=Path(self.temp_dir))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_valid_job_id_success(self) -> None:
        self.service.update_job_status("test-job-123", "processing")
        status = self.service.get_job_status("test-job-123")
        self.assertIsNotNone(status)
        self.assertEqual(status["status"], "processing")

    def test_invalid_job_ids_raise_value_error(self) -> None:
        invalid_ids = [
            "",
            "../test",
            "..\\test",
            "test/job",
            "test\\job",
            ".",
            "..",
        ]
        for job_id in invalid_ids:
            with self.subTest(job_id=job_id):
                with self.assertRaises(ValueError):
                    self.service.update_job_status(job_id, "processing")
                with self.assertRaises(ValueError):
                    self.service.get_job_status(job_id)

class JobApiTraversalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.service = JobService(storage_path=Path(self.temp_dir))

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_invalid_index_id_returns_400(self) -> None:
        invalid_ids = [
            "../test",
            "..\\test",
            "test/job",
            "test\\job",
            ".",
            "..",
        ]
        for idx_id in invalid_ids:
            with self.subTest(idx_id=idx_id):
                with self.assertRaises(HTTPException) as ctx:
                    get_indexing_status(index_id=idx_id, job_service=self.service)
                self.assertEqual(ctx.exception.status_code, 400)
