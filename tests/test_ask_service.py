import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ["LLM_PROVIDER"] = "openai"

from app.services.ask_service import AskIndexNotFoundError, AskService, AskServiceError
from app.services.retrieval_service import IndexNotFoundError, RetrievedChunk


class AskServiceTests(unittest.TestCase):
    def test_answers_question_with_retrieved_context_and_sources(self) -> None:
        retrieved_chunk = RetrievedChunk(
            text="def authenticate(token: str) -> bool: return bool(token)",
            file_path="auth.py",
            symbol_name="authenticate",
            chunk_type="function",
            similarity_score=0.93,
        )
        retrieval_service = Mock()
        retrieval_service.retrieve.return_value = [retrieved_chunk]
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="It validates a token."))]
        )

        with patch("app.services.ask_service.logger.info") as log_info:
            result = AskService(retrieval_service, client, model="gpt-4.1-mini").ask(
                "repository-1", "How is authentication handled?"
            )

        self.assertEqual(result.answer, "It validates a token.")
        self.assertEqual(result.sources[0].file_path, "auth.py")
        self.assertEqual(result.sources[0].symbol_name, "authenticate")
        self.assertEqual(result.sources[0].score, 0.93)
        retrieval_service.retrieve.assert_called_once_with(
            "repository-1", "How is authentication handled?"
        )
        request_arguments = client.chat.completions.create.call_args.kwargs
        self.assertEqual(request_arguments["model"], "gpt-4.1-mini")
        self.assertIn("File path: auth.py", request_arguments["messages"][1]["content"])
        self.assertIn("Symbol name: authenticate", request_arguments["messages"][1]["content"])
        self.assertIn("How is authentication handled?", request_arguments["messages"][1]["content"])
        log_info.assert_called_once()

    def test_maps_missing_indexes_to_clear_exception(self) -> None:
        retrieval_service = Mock()
        retrieval_service.retrieve.side_effect = IndexNotFoundError("Index 'missing' was not found.")

        with self.assertRaisesRegex(AskIndexNotFoundError, "missing"):
            AskService(retrieval_service, Mock()).ask("missing", "Where is the entry point?")

    def test_wraps_chat_completion_errors(self) -> None:
        retrieval_service = Mock()
        retrieval_service.retrieve.return_value = []
        client = Mock()
        client.chat.completions.create.side_effect = RuntimeError("service unavailable")

        with patch("app.services.ask_service.logger.exception"):
            with self.assertRaisesRegex(AskServiceError, "Unable to generate"):
                AskService(retrieval_service, client).ask("repository-1", "What does it do?")
