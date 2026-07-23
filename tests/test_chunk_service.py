import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.chunk_service import ChunkService, RepositoryChunkError, logger


class ChunkServiceTests(unittest.TestCase):
    def test_indexes_symbols_with_metadata_and_excludes_ignored_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            (repository / "src").mkdir()
            (repository / "src" / "module.py").write_text(
                "def top_level():\n    return 1\n\n"
                "class Greeter:\n"
                "    def greet(self, name):\n"
                "        def format_name():\n"
                "            return name.upper()\n"
                "        return format_name()\n",
                encoding="utf-8",
            )
            for ignored_directory in (
                ".git",
                ".venv",
                "venv",
                "node_modules",
                "dist",
                "build",
                "pycache",
                "__pycache__",
            ):
                ignored_path = repository / ignored_directory
                ignored_path.mkdir()
                (ignored_path / "ignored.py").write_text("def ignored(): pass\n", encoding="utf-8")

            with patch.object(logger, "info") as log_info:
                chunks, graph = ChunkService().index_repository(repository)

        by_name = {chunk.symbol_name: chunk for chunk in chunks}
        self.assertEqual(set(by_name), {"top_level", "Greeter", "greet", "format_name"})
        self.assertEqual(by_name["Greeter"].symbol_type, "class")
        self.assertIsNone(by_name["Greeter"].parent_symbol)
        self.assertEqual(by_name["greet"].symbol_type, "method")
        self.assertEqual(by_name["greet"].parent_symbol, "Greeter")
        self.assertEqual(by_name["format_name"].symbol_type, "function")
        self.assertEqual(by_name["format_name"].parent_symbol, "greet")
        self.assertEqual(by_name["top_level"].file_path, "src/module.py")
        self.assertEqual(by_name["top_level"].language, "python")
        self.assertEqual(by_name["top_level"].start_line, 1)
        self.assertEqual(by_name["top_level"].end_line, 2)
        self.assertEqual(
            by_name["top_level"].content.splitlines(),
            ["def top_level():", "    return 1"],
        )
        self.assertTrue(all(chunk.chunk_id for chunk in chunks))
        self.assertEqual(log_info.call_args.args[0], "Repository scan completed")
        log_metadata = log_info.call_args.kwargs["extra"]
        self.assertEqual(log_metadata["python_file_count"], 1)
        self.assertEqual(log_metadata["chunks_extracted"], 4)
        self.assertGreaterEqual(log_metadata["processing_time_seconds"], 0)

    def test_rejects_a_missing_repository(self) -> None:
        with self.assertRaises(RepositoryChunkError):
            ChunkService().index_repository(Path("missing-repository"))


if __name__ == "__main__":
    unittest.main()
