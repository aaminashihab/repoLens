import os
os.environ["LLM_PROVIDER"] = "openai"

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch, ANY

# pyrefly: ignore [missing-import]
from fastapi import HTTPException
# pyrefly: ignore [missing-import]
from fastapi.testclient import TestClient

import app.api.dependencies as api_deps
import app.api.routes.repositories as api_repos
import app.api.routes.ask as api_ask
from app.main import app as fastapi_app
from app.services.index_service import IndexService, IndexServiceError
from app.services.job_service import JobService
from app.services.chunk_service import CodeChunk
from app.services.embedding_service import EmbeddedChunk
from app.api.routes.repositories import delete_repository_index

class FakeVectors(list):
    @property
    def shape(self):
        return (len(self), len(self[0])) if self else (0, 0)

class RepoLensFeaturesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(fastapi_app)
        self.temp_dir = tempfile.mkdtemp()
        self.index_service = IndexService(storage_path=Path(self.temp_dir))
        self.job_service = JobService(storage_path=Path(self.temp_dir) / "jobs")
        
        # Save original dependency values/configs
        self.orig_api_key = api_deps.API_KEY
        self.orig_index_limit = api_repos.INDEX_RATE_LIMIT
        self.orig_ask_limit = api_ask.ASK_RATE_LIMIT

        # Set up a mock dependencies override if necessary
        api_deps.API_KEY = None

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        # Restore original dependencies/configs
        api_deps.API_KEY = self.orig_api_key
        api_repos.INDEX_RATE_LIMIT = self.orig_index_limit
        api_ask.ASK_RATE_LIMIT = self.orig_ask_limit
        # Clear dependency overrides
        fastapi_app.dependency_overrides.clear()
        # Clear rate limits stored in slowapi memory if any
        fastapi_app.state.limiter.reset()

    # --- Feature 1 Tests ---

    def test_api_key_auth_enforced_when_key_set(self) -> None:
        api_deps.API_KEY = "secure_secret_key"

        # Requesting /indexes without API key header -> should be 401
        res = self.client.get("/indexes")
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.json()["detail"], "Invalid or missing API key")

        # Requesting /indexes with incorrect API key header -> should be 401
        res = self.client.get("/indexes", headers={"X-API-Key": "wrong"})
        self.assertEqual(res.status_code, 401)

        # Requesting /indexes with correct API key header -> should be 200 (returns empty list initially)
        res = self.client.get("/indexes", headers={"X-API-Key": "secure_secret_key"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), [])

    def test_api_key_auth_bypassed_when_key_unset(self) -> None:
        api_deps.API_KEY = None

        # Requesting /indexes without API key header -> should be 200 (returns empty list)
        res = self.client.get("/indexes")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), [])

    def test_rate_limiting_trips_for_index_endpoint(self) -> None:
        api_deps.API_KEY = None
        # Set rate limit to 1/minute for indexing
        api_repos.INDEX_RATE_LIMIT = "1/minute"
        fastapi_app.state.limiter.reset()

        # Mock CloneService.validate_github_url to bypass git checks
        with patch("app.api.routes.repositories.CloneService.validate_github_url") as mock_validate:
            mock_validate.return_value = "https://github.com/owner/repo.git"

            # First request should succeed (returns 202)
            res1 = self.client.post("/index-repository", json={"repo_url": "https://github.com/owner/repo"})
            self.assertEqual(res1.status_code, 202)

            # Second request within the same minute should be rate limited (returns 429)
            res2 = self.client.post("/index-repository", json={"repo_url": "https://github.com/owner/repo"})
            self.assertEqual(res2.status_code, 429)
            self.assertIn("Rate limit exceeded", res2.json().get("error", ""))

    # --- Feature 2 Tests ---

    def test_index_service_build_list_delete_unit(self) -> None:
        # Mock FAISS and NumPy
        class FakeIndex:
            def __init__(self, d):
                self.d = d
                self.ntotal = 0
            def add(self, v):
                self.ntotal += len(v)

        def write_index(idx, p):
            Path(p).write_text("fake_index", encoding="utf-8")

        fake_faiss = SimpleNamespace(IndexFlatL2=FakeIndex, write_index=write_index)
        fake_numpy = SimpleNamespace(array=lambda v, dtype: FakeVectors(v))

        with patch.object(IndexService, "_faiss", return_value=fake_faiss), patch.object(IndexService, "_numpy", return_value=fake_numpy):
            # 1. Build Index with repo_url
            chunk = CodeChunk("id1", "file.py", "python", "sym", "func", None, 1, 2, "def sym(): pass")
            self.index_service.build_index("idx-test", [EmbeddedChunk(chunk, [1.0, 2.0])], repo_url="https://github.com/owner/repo")

            # Verify directory exists
            idx_dir = Path(self.temp_dir) / "idx-test"
            self.assertTrue(idx_dir.is_dir())
            self.assertTrue((idx_dir / "metadata.json").is_file())

            # 2. List Indexes
            indexes = self.index_service.list_indexes()
            self.assertEqual(len(indexes), 1)
            self.assertEqual(indexes[0]["index_id"], "idx-test")
            self.assertEqual(indexes[0]["repo_url"], "https://github.com/owner/repo")
            self.assertEqual(indexes[0]["chunk_count"], 1)
            self.assertIsNotNone(indexes[0]["created_at"])

            # 3. Delete Index
            deleted = self.index_service.delete_index("idx-test")
            self.assertTrue(deleted)
            self.assertFalse(idx_dir.is_dir())

            # Deleting again should return False
            deleted_again = self.index_service.delete_index("idx-test")
            self.assertFalse(deleted_again)

    def test_delete_route_success_and_failures(self) -> None:
        api_deps.API_KEY = None
        
        # Configure Mock IndexService and JobService
        mock_idx_srv = Mock()
        mock_idx_srv.delete_index.side_effect = lambda x: x == "existing-id"

        mock_job_srv = Mock()

        # Inject via FastAPI dependency overrides
        fastapi_app.dependency_overrides[api_deps.get_index_service] = lambda: mock_idx_srv
        fastapi_app.dependency_overrides[api_deps.get_job_service] = lambda: mock_job_srv

        # Test DELETE on existing index -> 200 OK
        res = self.client.delete("/index-repository/existing-id")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["message"], "Index and job status deleted successfully")
        mock_idx_srv.delete_index.assert_called_with("existing-id")
        mock_job_srv.delete_job_status.assert_called_with("existing-id")

        # Test DELETE on non-existing index -> 404
        res = self.client.delete("/index-repository/non-existent-id")
        self.assertEqual(res.status_code, 404)

        # Test DELETE on invalid index ID format -> 400
        # Call the endpoint function directly to bypass TestClient URL normalization
        mock_idx_srv.delete_index.side_effect = IndexServiceError("invalid format")
        with self.assertRaises(HTTPException) as ctx:
            delete_repository_index(index_id="../invalid", index_service=mock_idx_srv, job_service=mock_job_srv)
        self.assertEqual(ctx.exception.status_code, 400)

    # --- New Feature 1 Tests ---

    def test_delete_expired_indexes_unit(self) -> None:
        from datetime import datetime, timezone, timedelta
        # Create expired and active index folders
        expired_dir = Path(self.temp_dir) / "expired-idx"
        expired_dir.mkdir(parents=True, exist_ok=True)
        (expired_dir / "index.faiss").write_text("fake", encoding="utf-8")
        (expired_dir / "metadata.json").write_text(json.dumps({
            "created_at": (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat(),
            "repo_url": "https://github.com/owner/expired",
            "vector_count": 0,
            "chunks": []
        }), encoding="utf-8")

        active_dir = Path(self.temp_dir) / "active-idx"
        active_dir.mkdir(parents=True, exist_ok=True)
        (active_dir / "index.faiss").write_text("fake", encoding="utf-8")
        (active_dir / "metadata.json").write_text(json.dumps({
            "created_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            "repo_url": "https://github.com/owner/active",
            "vector_count": 0,
            "chunks": []
        }), encoding="utf-8")

        invalid_dir = Path(self.temp_dir) / "invalid-idx"
        invalid_dir.mkdir(parents=True, exist_ok=True)
        (invalid_dir / "metadata.json").write_text("not-a-json", encoding="utf-8")

        deleted = self.index_service.delete_expired_indexes(ttl_hours=3.0)
        self.assertEqual(deleted, ["expired-idx"])
        self.assertFalse(expired_dir.exists())
        self.assertTrue(active_dir.exists())
        self.assertTrue(invalid_dir.exists())

    def test_post_indexes_cleanup_route(self) -> None:
        api_deps.API_KEY = None
        mock_idx_srv = Mock()
        mock_idx_srv.delete_expired_indexes.return_value = ["deleted-1", "deleted-2"]
        mock_job_srv = Mock()

        fastapi_app.dependency_overrides[api_deps.get_index_service] = lambda: mock_idx_srv
        fastapi_app.dependency_overrides[api_deps.get_job_service] = lambda: mock_job_srv

        res = self.client.post("/indexes/cleanup")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"deleted": ["deleted-1", "deleted-2"]})
        mock_idx_srv.delete_expired_indexes.assert_called_once()
        self.assertEqual(mock_job_srv.delete_job_status.call_count, 2)

    # --- New Feature 2 Tests ---

    @patch("app.services.clone_service.Repo.clone_from")
    def test_private_repository_cloning(self, mock_clone_from) -> None:
        from app.services.clone_service import CloneService
        service = CloneService()

        # (a) Token passed is used but never logged
        with patch("app.services.clone_service.logger") as mock_logger:
            with patch.dict(os.environ, {"GITHUB_TOKEN": "env_token"}):
                # Request with explicit token
                res_path = service.clone_repository("https://github.com/owner/repo", github_token="my_secret_token")
                # Assert URL passed to git clone has the token
                mock_clone_from.assert_called_with("https://my_secret_token@github.com/owner/repo.git", ANY)
                # Assert token is not in logs
                for call in mock_logger.info.call_args_list:
                    log_str = str(call)
                    self.assertNotIn("my_secret_token", log_str)
                    self.assertNotIn("env_token", log_str)

        # (b) Cloning still works with no token
        mock_clone_from.reset_mock()
        with patch.dict(os.environ, {}, clear=True):
            res_path = service.clone_repository("https://github.com/owner/repo", github_token=None)
            mock_clone_from.assert_called_with("https://github.com/owner/repo.git", ANY)

        # (c) GITHUB_TOKEN fallback
        mock_clone_from.reset_mock()
        with patch.dict(os.environ, {"GITHUB_TOKEN": "fallback_token"}):
            res_path = service.clone_repository("https://github.com/owner/repo", github_token=None)
            mock_clone_from.assert_called_with("https://fallback_token@github.com/owner/repo.git", ANY)

    # --- New Feature 3 Tests ---

    def test_ask_request_history_truncation(self) -> None:
        from app.models.ask import AskRequest, ChatTurn
        turns = [ChatTurn(role="user", content=f"Q{i}") for i in range(15)]
        req = AskRequest(index_id="test-index", question="final Q", history=turns)
        self.assertEqual(len(req.history), 12)
        # Keeps the most recent 12 (Q3 to Q14)
        self.assertEqual(req.history[0].content, "Q3")
        self.assertEqual(req.history[-1].content, "Q14")

    @patch("app.services.ask_service.AskService._get_client")
    def test_ask_service_openai_history_inclusion(self, mock_get_client) -> None:
        from app.services.ask_service import AskService
        from app.services.retrieval_service import RetrievedChunk

        mock_client = Mock()
        mock_get_client.return_value = mock_client

        # Mock retrieval
        retrieved = [RetrievedChunk(text="def sym(): pass", file_path="file.py", symbol_name="sym", chunk_type="func", similarity_score=0.9)]
        mock_retrieval = Mock()
        mock_retrieval.retrieve.return_value = retrieved

        # Setup AskService with mock retrieval
        service = AskService(retrieval_service=mock_retrieval, client=mock_client)
        service._provider = "openai"

        # Mock OpenAI response
        mock_response = Mock()
        mock_response.choices = [Mock(message=Mock(content="Mocked Answer"))]
        mock_client.chat.completions.create.return_value = mock_response

        history = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"}
        ]
        res = service.ask("test-idx", "New Question", history=history)

        self.assertEqual(res.answer, "Mocked Answer")
        
        # Verify messages sent to OpenAI client
        call_args = mock_client.chat.completions.create.call_args[1]
        messages = call_args["messages"]
        self.assertEqual(len(messages), 4) # system, user, assistant, user-grounded
        self.assertEqual(messages[0]["content"], service._SYSTEM_PROMPT)
        self.assertEqual(messages[1], history[0])
        self.assertEqual(messages[2], history[1])
        self.assertIn("New Question", messages[3]["content"])
        self.assertIn("Repository context", messages[3]["content"])

    @patch("app.services.ask_service.AskService._get_client")
    def test_ask_service_gemini_history_inclusion(self, mock_get_client) -> None:
        from app.services.ask_service import AskService
        from app.services.retrieval_service import RetrievedChunk

        mock_client = Mock()
        mock_get_client.return_value = mock_client

        # Mock retrieval
        retrieved = [RetrievedChunk(text="def sym(): pass", file_path="file.py", symbol_name="sym", chunk_type="func", similarity_score=0.9)]
        mock_retrieval = Mock()
        mock_retrieval.retrieve.return_value = retrieved

        # Setup AskService with mock retrieval
        service = AskService(retrieval_service=mock_retrieval, client=mock_client)
        service._provider = "gemini"

        # Mock Gemini response
        mock_response = Mock()
        mock_response.text = "Mocked Gemini Answer"
        mock_client.models.generate_content.return_value = mock_response

        history = [
            {"role": "user", "content": "Hello Gemini"},
            {"role": "assistant", "content": "Hi Gemini user!"}
        ]
        res = service.ask("test-idx", "New Question", history=history)

        self.assertEqual(res.answer, "Mocked Gemini Answer")

        # Verify contents sent to Gemini client
        call_args = mock_client.models.generate_content.call_args[1]
        contents = call_args["contents"]
        self.assertIn("User: Hello Gemini", contents)
        self.assertIn("Assistant: Hi Gemini user!", contents)
        self.assertIn("New Question", contents)

