import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.services.chunk_service import CodeChunk
from app.services.index_service import IndexServiceError, LoadedIndex
from app.services.retrieval_service import IndexNotFoundError, RetrievalService


def make_chunk(name: str, file_path: str) -> CodeChunk:
    return CodeChunk(
        chunk_id=name,
        file_path=file_path,
        language="python",
        symbol_name=name,
        symbol_type="function",
        parent_symbol=None,
        start_line=1,
        end_line=2,
        content=f"def {name}(): pass",
    )


class RetrievalServiceTests(unittest.TestCase):
    def test_embeds_query_and_returns_ranked_chunks(self) -> None:
        faiss_index = Mock()
        faiss_index.d = 2
        faiss_index.search.return_value = ([[0.0, 3.0]], [[1, 0]])
        loaded_index = LoadedIndex(
            index=faiss_index,
            chunks=[make_chunk("first", "first.py"), make_chunk("second", "second.py")],
            index_path=Path("storage/indexes/example/index.faiss"),
        )
        index_service = Mock()
        index_service.load_index.return_value = loaded_index
        embedding_service = Mock()
        embedding_service.embed_query.return_value = [0.25, 0.75]

        with patch("app.services.retrieval_service.logger.info") as log_info:
            results = RetrievalService(index_service, embedding_service).retrieve(
                "example", "Where is the second function?"
            )

        embedding_service.embed_query.assert_called_once_with("Where is the second function?")
        self.assertEqual([result.symbol_name for result in results], ["second", "first"])
        self.assertEqual(results[0].file_path, "second.py")
        self.assertEqual(results[0].chunk_type, "function")
        self.assertEqual(results[0].similarity_score, 1.0)
        self.assertEqual(results[1].similarity_score, 0.25)
        log_info.assert_called_once()

    def test_missing_index_raises_clear_exception(self) -> None:
        index_service = Mock()
        index_service.load_index.side_effect = IndexServiceError("Index 'missing' does not exist.")

        with self.assertRaisesRegex(IndexNotFoundError, "missing"):
            RetrievalService(index_service, Mock()).retrieve("missing", "find a function")

    def test_empty_index_returns_no_chunks_without_embedding(self) -> None:
        index_service = Mock()
        index_service.load_index.return_value = SimpleNamespace(
            chunks=[], index=SimpleNamespace(d=1536)
        )
        embedding_service = Mock()

        self.assertEqual(
            RetrievalService(index_service, embedding_service).retrieve("empty", "find a function"),
            [],
        )
        embedding_service.embed_query.assert_not_called()
