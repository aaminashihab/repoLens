"""Tree-sitter based Python source-code chunking."""

import logging
import os
from time import perf_counter
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from tree_sitter import Language, Node, Parser
import tree_sitter_python


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
    """Extract and retain Python symbol chunks from cloned repositories."""

    _EXCLUDED_DIRECTORIES = {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "dist",
        "build",
        "pycache",
        "__pycache__",
    }

    def __init__(self) -> None:
        language = Language(tree_sitter_python.language())
        self._parser = Parser(language)
        self._chunks: list[CodeChunk] = []

    @property
    def chunks(self) -> tuple[CodeChunk, ...]:
        """Return all chunks retained in memory so far."""
        return tuple(self._chunks)

    def index_repository(self, repository_path: Path) -> list[CodeChunk]:
        """Parse every Python file below ``repository_path`` and store its chunks."""
        if not repository_path.is_dir():
            raise RepositoryChunkError(
                f"Repository path does not exist or is not a directory: {repository_path}"
            )

        started_at = perf_counter()
        python_files = self._find_python_files(repository_path)
        indexed_chunks: list[CodeChunk] = []

        for file_path in python_files:
            try:
                source = file_path.read_bytes()
            except OSError as exc:
                raise RepositoryChunkError(f"Unable to read Python file: {file_path}") from exc

            relative_path = file_path.relative_to(repository_path).as_posix()
            tree = self._parser.parse(source)
            indexed_chunks.extend(
                self._extract_chunks(tree.root_node, source, relative_path)
            )

        self._chunks.extend(indexed_chunks)
        processing_time_seconds = perf_counter() - started_at
        logger.info(
            "Repository scan completed",
            extra={
                "repository_path": str(repository_path),
                "python_file_count": len(python_files),
                "chunks_extracted": len(indexed_chunks),
                "processing_time_seconds": processing_time_seconds,
            },
        )
        return indexed_chunks

    def clear_chunks(self) -> None:
        """Clear the in-memory chunk store."""
        self._chunks.clear()

    @classmethod
    def _find_python_files(cls, repository_path: Path) -> list[Path]:
        """Return Python files while pruning directories that should not be indexed."""
        python_files: list[Path] = []
        for directory, subdirectories, files in os.walk(repository_path):
            subdirectories[:] = sorted(
                name for name in subdirectories if name not in cls._EXCLUDED_DIRECTORIES
            )
            python_files.extend(
                Path(directory) / name for name in sorted(files) if name.endswith(".py")
            )
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

        return chunks
