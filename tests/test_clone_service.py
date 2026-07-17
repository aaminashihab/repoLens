import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from git import GitError
from fastapi.testclient import TestClient

from app.main import app
from app.services.clone_service import (
    CloneService,
    InvalidRepositoryUrlError,
    RepositoryCloneError,
)


class CloneServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = CloneService()

    def test_validate_github_url_success(self) -> None:
        valid_url = "https://github.com/owner/repo"
        normalized = self.service.validate_github_url(valid_url)
        self.assertEqual(normalized, "https://github.com/owner/repo.git")

        valid_git_url = "https://github.com/owner/repo.git"
        normalized_git = self.service.validate_github_url(valid_git_url)
        self.assertEqual(normalized_git, "https://github.com/owner/repo.git")

    def test_validate_github_url_invalid(self) -> None:
        invalid_urls = [
            "http://github.com/owner/repo",
            "https://notgithub.com/owner/repo",
            "https://github.com/owner",
            "https://github.com/owner/repo/extra",
            "https://github.com/owner/repo?query=1",
        ]
        for url in invalid_urls:
            with self.subTest(url=url):
                with self.assertRaises(InvalidRepositoryUrlError):
                    self.service.validate_github_url(url)

    @patch("app.services.clone_service.Repo.clone_from")
    def test_clone_repository_success(self, mock_clone) -> None:
        with patch("app.services.clone_service.tempfile.mkdtemp") as mock_mkdtemp:
            temp_dir = tempfile.mkdtemp(prefix="test-repolens-")
            mock_mkdtemp.return_value = temp_dir
            try:
                repo_path = self.service.clone_repository("https://github.com/owner/repo")
                self.assertEqual(repo_path, Path(temp_dir) / "repository")
                mock_clone.assert_called_once_with("https://github.com/owner/repo.git", repo_path)
            finally:
                import shutil
                shutil.rmtree(temp_dir, ignore_errors=True)

    @patch("app.services.clone_service.Repo.clone_from")
    def test_clone_repository_failure_cleans_up(self, mock_clone) -> None:
        mock_clone.side_effect = GitError("clone failed")
        with patch("app.services.clone_service.tempfile.mkdtemp") as mock_mkdtemp:
            temp_dir = tempfile.mkdtemp(prefix="test-repolens-")
            mock_mkdtemp.return_value = temp_dir
            
            with self.assertRaises(RepositoryCloneError):
                self.service.clone_repository("https://github.com/owner/repo")
            
            # The directory should be cleaned up on failure
            self.assertFalse(Path(temp_dir).exists())

    @patch("app.services.clone_service.Repo.clone_from")
    def test_clone_repository_context_cleanup_on_success(self, mock_clone) -> None:
        with patch("app.services.clone_service.tempfile.mkdtemp") as mock_mkdtemp:
            temp_dir = tempfile.mkdtemp(prefix="test-repolens-")
            mock_mkdtemp.return_value = temp_dir
            
            # Ensure the directory exists initially for mock
            Path(temp_dir).mkdir(parents=True, exist_ok=True)
            
            with self.service.clone_repository_context("https://github.com/owner/repo") as repo_path:
                self.assertEqual(repo_path, Path(temp_dir) / "repository")
                self.assertTrue(Path(temp_dir).exists())
            
            # After context block, the directory should be removed
            self.assertFalse(Path(temp_dir).exists())

    @patch("app.services.clone_service.Repo.clone_from")
    def test_clone_repository_context_cleanup_on_failure(self, mock_clone) -> None:
        with patch("app.services.clone_service.tempfile.mkdtemp") as mock_mkdtemp:
            temp_dir = tempfile.mkdtemp(prefix="test-repolens-")
            mock_mkdtemp.return_value = temp_dir
            
            Path(temp_dir).mkdir(parents=True, exist_ok=True)
            
            with self.assertRaises(RuntimeError):
                with self.service.clone_repository_context("https://github.com/owner/repo") as repo_path:
                    self.assertTrue(Path(temp_dir).exists())
                    raise RuntimeError("Something failed inside context")
            
            # After context block exits due to exception, the directory should still be removed
            self.assertFalse(Path(temp_dir).exists())


class RepositoryApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    @patch("app.api.routes.repositories.BackgroundTasks.add_task")
    @patch("app.api.routes.repositories.CloneService.validate_github_url")
    def test_index_repository_endpoint_returns_202_and_adds_task(self, mock_validate, mock_add_task) -> None:
        mock_validate.return_value = "https://github.com/owner/repo.git"
        
        response = self.client.post(
            "/index-repository",
            json={"repo_url": "https://github.com/owner/repo"}
        )
        
        self.assertEqual(response.status_code, 202)
        data = response.json()
        self.assertIn("index_id", data)
        self.assertEqual(data["status"], "processing")
        mock_add_task.assert_called_once()
        
        # Test status endpoint
        index_id = data["index_id"]
        status_response = self.client.get(f"/index-repository/{index_id}")
        self.assertEqual(status_response.status_code, 200)
        status_data = status_response.json()
        self.assertEqual(status_data["status"], "processing")
