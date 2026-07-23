"""Tree-sitter based Python source-code chunking and call graph building."""

import logging
import os
from time import perf_counter
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from tree_sitter import Language, Node, Parser
import tree_sitter_python

from app.core.graph import CodeNode, RepositoryGraph


logger = logging.getLogger(__name__)


class RepositoryChunkError(RuntimeError):
    """Raised when a repository directory cannot be indexed."""


@dataclass(frozen=True, slots=True)
class CodeChunk:
    """Metadata and source content for one extracted Python symbol."""

    chunk_id: str
    file_path: str
    language: str
    symbol_name: str
    symbol_type: str
    parent_symbol: str | None
    start_line: int
    end_line: int
    content: str


class ChunkService:
    """Extract Python symbol chunks and build call-graphs from cloned repositories."""

    _EXCLUDED_DIRECTORIES = {
        ".git", ".venv", "venv", "node_modules", "dist", "build",
        "pycache", "__pycache__", ".tox", ".mypy_cache", ".pytest_cache",
        "vendor", "third_party", "extern",
    }
    _MAX_SOURCE_FILES = 5000
    # Per-file read limit: files larger than this are skipped (prevents memory exhaustion)
    _MAX_FILE_BYTES = 512 * 1024  # 512 KB
    # Total repository byte budget across all files (prevents zip-bomb style repos)
    _MAX_TOTAL_BYTES = 50 * 1024 * 1024  # 50 MB

    _SUPPORTED_EXTENSIONS = {
        ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".c", ".cpp",
        ".h", ".sh", ".sql", ".html", ".css", ".yaml", ".yml", ".json", ".md"
    }

    def __init__(self) -> None:
        self._language = Language(tree_sitter_python.language())

    def index_repository(
        self, repository_path: Path
    ) -> tuple[list[CodeChunk], RepositoryGraph]:
        """Parse source files below ``repository_path``, extracting AST chunks and building graph."""
        if not repository_path.is_dir():
            raise RepositoryChunkError(
                f"Repository path does not exist or is not a directory: {repository_path}"
            )

        started_at = perf_counter()
        source_files, python_files = self._find_source_files(repository_path)
        if len(source_files) > self._MAX_SOURCE_FILES:
            raise RepositoryChunkError(
                f"Repository is too large: {len(source_files)} source files found "
                f"(max {self._MAX_SOURCE_FILES})."
            )

        indexed_chunks: list[CodeChunk] = []
        graph = RepositoryGraph()

        for file_path in source_files:
            try:
                file_stat = file_path.stat()
                if file_stat.st_size > self._MAX_FILE_BYTES:
                    logger.warning(
                        "Skipping oversized file",
                        extra={"file": str(file_path), "size_bytes": file_stat.st_size},
                    )
                    continue
                source = file_path.read_bytes()
            except OSError as exc:
                raise RepositoryChunkError(f"Unable to read file: {file_path}") from exc

            # Security: skip binary files (null bytes indicate non-text content)
            if b"\x00" in source[:1024]:
                logger.debug("Skipping binary file", extra={"file": str(file_path)})
                continue

            if not source.strip():
                continue

            relative_path = file_path.relative_to(repository_path).as_posix()
            
            if file_path.suffix == ".py":
                parser = Parser(self._language)
                tree = parser.parse(source)
                file_chunks = self._extract_chunks(tree.root_node, source, relative_path)
                # Extract cross-symbol function call edges for Python ASTs
                self._extract_graph_edges(tree.root_node, source, relative_path, graph)
            else:
                file_chunks = self._extract_generic_chunks(source, relative_path)

            indexed_chunks.extend(file_chunks)

            # Build graph nodes
            for chunk in file_chunks:
                node_id = f"{chunk.file_path}::{chunk.symbol_name}"
                graph.add_node(
                    CodeNode(
                        node_id=node_id,
                        file_path=chunk.file_path,
                        symbol_name=chunk.symbol_name,
                        symbol_type=chunk.symbol_type,
                        start_line=chunk.start_line,
                        end_line=chunk.end_line,
                        content=chunk.content,
                    )
                )

        processing_time_seconds = perf_counter() - started_at
        logger.info(
            "Repository scan completed",
            extra={
                "repository_path": str(repository_path),
                "python_file_count": len(python_files),
                "source_file_count": len(source_files),
                "chunks_extracted": len(indexed_chunks),
                "graph_nodes": len(graph.nodes),
                "graph_edges": len(graph.edges),
                "processing_time_seconds": processing_time_seconds,
            },
        )
        return indexed_chunks, graph

    @classmethod
    def _find_source_files(cls, repository_path: Path) -> tuple[list[Path], list[Path]]:
        """Return all supported source files and Python files while pruning excluded directories.

        Security hardening applied:
        - Symlinks are skipped (prevents reading system files via crafted symlinks)
        - Path traversal checked (file must resolve inside repository_path)
        - Files exceeding _MAX_FILE_BYTES are reported and deferred to caller
        - Binary files detected early via null-byte check
        """
        source_files: list[Path] = []
        python_files: list[Path] = []
        resolved_root = repository_path.resolve()
        total_bytes = 0

        for directory, subdirectories, files in os.walk(repository_path):
            # Security: skip symlinked directories (could point outside temp dir)
            subdirectories[:] = sorted(
                name for name in subdirectories
                if name not in cls._EXCLUDED_DIRECTORIES
                and not Path(directory, name).is_symlink()
            )
            for name in sorted(files):
                p = Path(directory) / name

                # Security: skip symlinked files
                if p.is_symlink():
                    logger.warning("Skipping symlink file", extra={"file": str(p)})
                    continue

                # Security: path traversal guard — resolved path must stay inside repo root
                try:
                    p.resolve().relative_to(resolved_root)
                except ValueError:
                    logger.warning("Skipping path traversal attempt", extra={"file": str(p)})
                    continue

                if p.suffix not in cls._SUPPORTED_EXTENSIONS:
                    continue

                # Track cumulative size to prevent zip-bomb repos
                try:
                    file_size = p.stat().st_size
                except OSError:
                    continue
                total_bytes += file_size
                if total_bytes > cls._MAX_TOTAL_BYTES:
                    logger.warning(
                        "Total repository byte budget exceeded; stopping file scan",
                        extra={"total_bytes_so_far": total_bytes, "limit": cls._MAX_TOTAL_BYTES},
                    )
                    return source_files, python_files

                source_files.append(p)
                if p.suffix == ".py":
                    python_files.append(p)

        return source_files, python_files

    @classmethod
    def _find_python_files(cls, repository_path: Path) -> list[Path]:
        _, python_files = cls._find_source_files(repository_path)
        return python_files

    def _extract_chunks(
        self, root_node: Node, source: bytes, file_path: str
    ) -> list[CodeChunk]:
        chunks: list[CodeChunk] = []

        def visit(
            node: Node,
            parent_name: str | None = None,
            parent_type: str | None = None,
        ) -> None:
            current_name = parent_name
            current_type = parent_type
            if node.type in {"function_definition", "class_definition"}:
                name_node = node.child_by_field_name("name")
                if name_node is not None:
                    current_name = source[name_node.start_byte : name_node.end_byte].decode(
                        "utf-8", errors="replace"
                    )
                    current_type = "class" if node.type == "class_definition" else "function"
                    symbol_type = (
                        "method"
                        if node.type == "function_definition" and parent_type == "class"
                        else current_type
                    )
                    chunks.append(
                        CodeChunk(
                            chunk_id=str(uuid4()),
                            file_path=file_path,
                            language="python",
                            symbol_name=current_name,
                            symbol_type=symbol_type,
                            parent_symbol=parent_name,
                            start_line=node.start_point.row + 1,
                            end_line=node.end_point.row + 1,
                            content=source[node.start_byte : node.end_byte].decode(
                                "utf-8", errors="replace"
                            ),
                        )
                    )

            for child in node.children:
                visit(child, current_name, current_type)

        visit(root_node)
        if not chunks and source.strip():
            # Fallback module-level chunk for top-level code or scripts with no def/class AST nodes
            file_name = Path(file_path).name
            content_str = source.decode("utf-8", errors="replace")
            # BUG-NEW-2 FIX: truncate content to cap, then compute end_line from actual
            # truncated content — not the original file line count.
            truncated = content_str[:4000]
            truncated_lines = truncated.splitlines()
            chunks.append(
                CodeChunk(
                    chunk_id=str(uuid4()),
                    file_path=file_path,
                    language="python",
                    symbol_name=file_name,
                    symbol_type="module",
                    parent_symbol=None,
                    start_line=1,
                    end_line=max(1, len(truncated_lines)),
                    content=truncated,
                )
            )
        return chunks

    def _extract_generic_chunks(self, source: bytes, file_path: str) -> list[CodeChunk]:
        """Extract structured code chunks for non-Python source files."""
        chunks: list[CodeChunk] = []
        content_str = source.decode("utf-8", errors="replace")
        lines = content_str.splitlines()
        if not lines:
            return chunks

        file_name = Path(file_path).name
        ext = Path(file_path).suffix.lstrip(".")
        lang = ext if ext else "plaintext"

        # BUG-NEW-3 FIX: Use consistent block-N labeling for ALL blocks (1-indexed)
        # Previously: first block had no number, second jumped to #block-2 (skipping #block-1)
        block_size = 60
        for i in range(0, len(lines), block_size):
            chunk_lines = lines[i : i + block_size]
            chunk_text = "\n".join(chunk_lines)
            if not chunk_text.strip():
                continue
            block_num = i // block_size + 1
            symbol_label = file_name if block_num == 1 else f"{file_name}#block-{block_num}"
            chunks.append(
                CodeChunk(
                    chunk_id=str(uuid4()),
                    file_path=file_path,
                    language=lang,
                    symbol_name=symbol_label,
                    symbol_type="file_block",
                    parent_symbol=None,
                    start_line=i + 1,
                    end_line=i + len(chunk_lines),
                    content=chunk_text,
                )
            )
        return chunks

    def _extract_graph_edges(
        self, root_node: Node, source: bytes, file_path: str, graph: RepositoryGraph
    ) -> None:
        """Traverse AST to detect call invocations and build graph edges."""

        def visit_calls(node: Node, current_symbol: str | None = None) -> None:
            if node.type in {"function_definition", "class_definition"}:
                name_node = node.child_by_field_name("name")
                if name_node:
                    current_symbol = source[name_node.start_byte : name_node.end_byte].decode(
                        "utf-8", errors="replace"
                    )

            if node.type == "call" and current_symbol:
                func_node = node.child_by_field_name("function")
                if func_node:
                    called_name = source[func_node.start_byte : func_node.end_byte].decode(
                        "utf-8", errors="replace"
                    )
                    # Extract final function name if attribute call e.g. self.foo() -> foo
                    if "." in called_name:
                        called_name = called_name.split(".")[-1]

                    source_id = f"{file_path}::{current_symbol}"
                    # Match target symbol in graph
                    target_nodes = graph.find_nodes_by_symbol(called_name)
                    for tnode in target_nodes:
                        if tnode.node_id != source_id:
                            graph.add_edge(source_id, tnode.node_id, "calls")

            for child in node.children:
                visit_calls(child, current_symbol)

        visit_calls(root_node)
