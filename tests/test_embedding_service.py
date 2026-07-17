import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ["LLM_PROVIDER"] = "openai"

from app.services.chunk_service import CodeChunk
from app.services.embedding_service import EmbeddingService, EmbeddingServiceError


def make_chunk(name: str) -> CodeChunk:
    return CodeChunk(name, "module.py", "python", name, "function", None, 1, 2, f"def {name}(): pass")


class EmbeddingServiceTests(unittest.TestCase):
    def test_batches_chunks_and_preserves_chunk_order(self) -> None:
        client = Mock()
        client.embeddings.create.side_effect = [
            SimpleNamespace(data=[SimpleNamespace(index=1, embedding=[2.0]), SimpleNamespace(index=0, embedding=[1.0])]),
            SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[3.0])]),
        ]

        embedded = EmbeddingService(client, batch_size=2).embed_chunks(
            [make_chunk("one"), make_chunk("two"), make_chunk("three")]
        )

        self.assertEqual([item.chunk.symbol_name for item in embedded], ["one", "two", "three"])
        self.assertEqual([item.embedding for item in embedded], [[1.0], [2.0], [3.0]])
        self.assertEqual(client.embeddings.create.call_count, 2)

    def test_wraps_api_errors(self) -> None:
        client = Mock()
        client.embeddings.create.side_effect = RuntimeError("service unavailable")

        with patch("app.services.embedding_service.logger.exception"):
            with self.assertRaises(EmbeddingServiceError):
                EmbeddingService(client).embed_chunks([make_chunk("one")])
