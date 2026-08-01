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

    def test_indexes_jsts_symbols_and_extracts_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            (repository / "src").mkdir()
            (repository / "src" / "auth.ts").write_text(
                "export async function loginUser(username, password) {\n"
                "    return validateToken(username);\n"
                "}\n\n"
                "export class AuthService {\n"
                "    async checkSession() {\n"
                "        return true;\n"
                "    }\n"
                "}\n",
                encoding="utf-8",
            )
            chunks, graph = ChunkService().index_repository(repository)

        by_name = {chunk.symbol_name: chunk for chunk in chunks}
        self.assertIn("loginUser", by_name)
        self.assertIn("AuthService", by_name)
        self.assertEqual(by_name["loginUser"].language, "typescript")
        self.assertEqual(by_name["loginUser"].symbol_type, "function")
        self.assertEqual(by_name["AuthService"].symbol_type, "class")

    def test_jsts_graph_edges_generated_for_cross_file_calls(self) -> None:
        """Documents how JS/TS graph edges behave across files.

        The JS/TS edge builder operates in a single indexing pass: edges are only
        created to symbols already present in the graph when a file is processed.
        Files are walked in sorted order, so a caller file can only reference
        callees from files that sort earlier alphabetically.

        Known limitation: symbols defined in files processed *after* the caller
        file produce no graph edge, even if a real call exists in source. This
        is distinct from the Python path, which uses a tree-sitter AST and builds
        the full graph before edge resolution.
        """
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            # autils.ts sorts before report.ts, so its symbols ARE in the graph
            # when report.ts is processed — edges should be created.
            (repository / "autils.ts").write_text(
                "export function formatDate(d: Date): string {\n"
                "    return d.toISOString();\n"
                "}\n",
                encoding="utf-8",
            )
            (repository / "report.ts").write_text(
                "import { formatDate } from './autils';\n"
                "export function generateReport(date: Date): string {\n"
                "    return formatDate(date);\n"
                "}\n",
                encoding="utf-8",
            )
            chunks, graph = ChunkService().index_repository(repository)

        # autils.ts is processed first (sorts before report.ts), so formatDate
        # is in the graph when report.ts is scanned — an import edge is expected.
        edge_targets = {edge.target_id for edge in graph.edges}
        autils_nodes = {node.node_id for node in graph.nodes.values() if "autils.ts" in node.file_path}
        self.assertTrue(
            edge_targets & autils_nodes,
            "Expected at least one graph edge pointing at an autils.ts symbol. "
            "If this fails, the import/call pattern may have changed.",
        )

    def test_jsts_call_pattern_known_limitation_single_pass(self) -> None:
        """Documents that JS/TS graph edges are silently dropped for not-yet-indexed symbols.

        Because the JS/TS edge builder runs in a single file-walk pass, it can
        only create edges to symbols already in the graph. Symbols in files
        processed *after* the caller produce no edge — even if ghostHelper()
        appears in source code (not just comments).

        This is a known limitation: the Python path resolves this by building
        the full symbol graph first, then resolving edges in a second pass.
        The fix for JS/TS would be a two-pass approach or buffering unresolved
        edge targets.
        """
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory)
            # caller.ts sorts before real.ts — so real.ts is processed AFTER caller.ts.
            # ghostHelper is therefore not in the graph when caller.ts is scanned.
            (repository / "caller.ts").write_text(
                "export function doWork(): void {\n"
                "    ghostHelper();\n"  # real call, not a comment
                "}\n",
                encoding="utf-8",
            )
            (repository / "real.ts").write_text(
                "export function ghostHelper(): void {}\n",
                encoding="utf-8",
            )
            chunks, graph = ChunkService().index_repository(repository)

        ghost_nodes = {node.node_id for node in graph.nodes.values() if "ghostHelper" in node.node_id}
        edge_targets = {edge.target_id for edge in graph.edges}
        # Known limitation: no edge is created because ghostHelper isn't in the graph
        # yet when caller.ts is processed (single-pass walk, alphabetical order).
        self.assertFalse(
            edge_targets & ghost_nodes,
            "Behaviour changed: cross-file edge created for a symbol indexed after its caller. "
            "The single-pass limitation may have been resolved — update this test.",
        )


if __name__ == "__main__":
    unittest.main()
