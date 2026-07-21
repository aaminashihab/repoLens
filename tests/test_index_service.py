import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.services.chunk_service import CodeChunk
from app.services.embedding_service import EmbeddedChunk
from app.services.index_service import IndexService, IndexServiceError


class FakeVectors(list):
    @property
    def shape(self):
        return (len(self), len(self[0])) if self else (0, 0)


class FakeIndex:
    def __init__(self, dimension):
        self.d = dimension
        self.ntotal = 0

    def add(self, vectors):
        self.ntotal += len(vectors)


class IndexServiceTests(unittest.TestCase):
    def test_builds_and_loads_index_with_chunk_metadata(self) -> None:
        chunk = CodeChunk("one", "module.py", "python", "one", "function", None, 1, 2, "def one(): pass")
        saved_indexes = {}

        def write_index(index, path):
            Path(path).write_text("fake", encoding="utf-8")
            saved_indexes[path] = index

        fake_faiss = SimpleNamespace(IndexFlatL2=FakeIndex, write_index=write_index, read_index=saved_indexes.__getitem__)
        fake_numpy = SimpleNamespace(array=lambda vectors, dtype: FakeVectors(vectors))
        with tempfile.TemporaryDirectory() as directory, patch.object(IndexService, "_faiss", return_value=fake_faiss), patch.object(IndexService, "_numpy", return_value=fake_numpy):
            service = IndexService(Path(directory))
            built = service.build_index("index-1", [EmbeddedChunk(chunk, [0.1, 0.2])])
            loaded = service.load_index("index-1")

        self.assertEqual(built.index.ntotal, 1)
        self.assertEqual(loaded.chunks, [chunk])

    def test_rejects_invalid_index_id(self) -> None:
        with self.assertRaises(IndexServiceError):
            IndexService().build_index("../outside", [])

    def test_build_empty_index_both_providers(self) -> None:
        from app.services.embedding_service import EmbeddingService

        saved_indexes = {}
        def write_index(index, path):
            Path(path).write_text("fake", encoding="utf-8")
            saved_indexes[path] = index

        fake_faiss = SimpleNamespace(IndexFlatL2=FakeIndex, write_index=write_index)
        
        with tempfile.TemporaryDirectory() as directory, patch.object(IndexService, "_faiss", return_value=fake_faiss):
            service = IndexService(Path(directory))
            
            # OpenAI / text-embedding-3-small (default)
            openai_service = EmbeddingService(model="text-embedding-3-small")
            self.assertEqual(openai_service.embedding_dimension, 1536)
            
            built_openai = service.build_index("empty-openai", [], dimension=openai_service.embedding_dimension)
            self.assertEqual(built_openai.index.d, 1536)
            self.assertEqual(built_openai.index.ntotal, 0)
            
            # Gemini / text-embedding-004
            gemini_service = EmbeddingService(model="text-embedding-004")
            self.assertEqual(gemini_service.embedding_dimension, 768)
            
            built_gemini = service.build_index("empty-gemini", [], dimension=gemini_service.embedding_dimension)
            self.assertEqual(built_gemini.index.d, 768)
            self.assertEqual(built_gemini.index.ntotal, 0)
