"""Persistent FAISS indexes for embedded repository chunks."""

import json
import logging
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.chunk_service import CodeChunk
from app.services.embedding_service import EmbeddedChunk


logger = logging.getLogger(__name__)


class IndexServiceError(RuntimeError):
    """Raised when a FAISS index cannot be built or loaded."""


@dataclass(frozen=True, slots=True)
class LoadedIndex:
    """A FAISS index and the metadata needed to identify its vectors."""

    index: Any
    chunks: list[CodeChunk]
    index_path: Path


class IndexService:
    """Build and load durable FAISS indexes with chunk metadata."""

    _INDEX_FILENAME = "index.faiss"
    _METADATA_FILENAME = "metadata.json"

    def __init__(self, storage_path: Path = Path("storage/indexes")) -> None:
        self._storage_path = storage_path

    def build_index(self, index_id: str, embedded_chunks: list[EmbeddedChunk], repo_url: str = "") -> LoadedIndex:
        """Build and persist a FAISS L2 index under ``storage/indexes/index_id``."""
        self._validate_index_id(index_id)
        faiss = self._faiss()
        index_directory = self._storage_path / index_id
        index_directory.mkdir(parents=True, exist_ok=True)
        index_path = index_directory / self._INDEX_FILENAME
        metadata_path = index_directory / self._METADATA_FILENAME

        try:
            if embedded_chunks:
                np = self._numpy()
                vectors = np.array([item.embedding for item in embedded_chunks], dtype="float32")
                if len(vectors.shape) != 2 or vectors.shape[1] == 0:
                    raise IndexServiceError("Embeddings must be non-empty, equally sized vectors.")
                index = faiss.IndexFlatL2(vectors.shape[1])
                index.add(vectors)
            else:
                index = faiss.IndexFlatL2(1536)

            faiss.write_index(index, str(index_path))
            created_at = datetime.now(timezone.utc).isoformat()
            metadata = {
                "vector_count": len(embedded_chunks),
                "dimension": index.d,
                "chunks": [asdict(item.chunk) for item in embedded_chunks],
                "repo_url": repo_url,
                "created_at": created_at,
            }
            temporary_metadata_path = metadata_path.with_suffix(".json.tmp")
            temporary_metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            temporary_metadata_path.replace(metadata_path)
        except IndexServiceError:
            raise
        except Exception as exc:
            logger.exception("Failed to persist FAISS index", extra={"index_id": index_id})
            raise IndexServiceError(f"Unable to build index '{index_id}'.") from exc

        logger.info(
            "FAISS index built",
            extra={
                "index_id": index_id,
                "faiss_index_size": index.ntotal,
                "index_path": str(index_directory),
            },
        )
        return LoadedIndex(index=index, chunks=[item.chunk for item in embedded_chunks], index_path=index_path)

    def load_index(self, index_id: str) -> LoadedIndex:
        """Load a previously persisted FAISS index and its chunk metadata."""
        self._validate_index_id(index_id)
        index_directory = self._storage_path / index_id
        index_path = index_directory / self._INDEX_FILENAME
        metadata_path = index_directory / self._METADATA_FILENAME
        if not index_path.is_file() or not metadata_path.is_file():
            raise IndexServiceError(f"Index '{index_id}' does not exist.")

        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            chunks = [CodeChunk(**chunk) for chunk in metadata["chunks"]]
            index = self._faiss().read_index(str(index_path))
        except Exception as exc:
            logger.exception("Failed to load FAISS index", extra={"index_id": index_id})
            raise IndexServiceError(f"Unable to load index '{index_id}'.") from exc

        if index.ntotal != len(chunks):
            raise IndexServiceError(f"Index '{index_id}' has inconsistent vector metadata.")
        return LoadedIndex(index=index, chunks=chunks, index_path=index_path)

    def list_indexes(self) -> list[dict[str, Any]]:
        """List summaries of all persisted FAISS indexes."""
        indexes = []
        if not self._storage_path.is_dir():
            return []
            
        for path in self._storage_path.iterdir():
            if path.is_dir():
                metadata_path = path / self._METADATA_FILENAME
                if metadata_path.is_file():
                    try:
                        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                        chunks = metadata.get("chunks", [])
                        indexes.append({
                            "index_id": path.name,
                            "repo_url": metadata.get("repo_url", ""),
                            "vector_count": metadata.get("vector_count", 0),
                            "chunk_count": len(chunks) if isinstance(chunks, list) else 0,
                            "created_at": metadata.get("created_at", ""),
                        })
                    except Exception as exc:
                        logger.warning(
                            "Failed to parse metadata file for index directory",
                            extra={"directory": path.name, "error": str(exc)}
                        )
        return indexes

    def delete_index(self, index_id: str) -> bool:
        """Delete a persisted index by ID."""
        self._validate_index_id(index_id)
        index_directory = self._storage_path / index_id
        if not index_directory.is_dir():
            return False
        shutil.rmtree(index_directory, ignore_errors=True)
        return True

    def delete_expired_indexes(self, ttl_hours: float) -> list[str]:
        """Delete all indexes that have been created more than ttl_hours ago."""
        deleted_ids = []
        indexes = self.list_indexes()
        now = datetime.now(timezone.utc)
        for idx in indexes:
            index_id = idx["index_id"]
            created_at_str = idx.get("created_at")
            if not created_at_str:
                logger.warning(
                    "Index is missing created_at timestamp; skipping expiration cleanup",
                    extra={"index_id": index_id}
                )
                continue
            try:
                created_at = datetime.fromisoformat(created_at_str)
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
                age = now - created_at
                if age.total_seconds() > ttl_hours * 3600:
                    if self.delete_index(index_id):
                        deleted_ids.append(index_id)
            except Exception as exc:
                logger.warning(
                    "Index has unparseable created_at timestamp; skipping expiration cleanup",
                    extra={"index_id": index_id, "created_at": created_at_str, "error": str(exc)}
                )
        return deleted_ids


    @staticmethod
    def _validate_index_id(index_id: str) -> None:
        if (
            not index_id
            or "/" in index_id
            or "\\" in index_id
            or index_id in {".", ".."}
        ):
            raise IndexServiceError("Index ID must be a single directory name.")

    @staticmethod
    def _faiss() -> Any:
        try:
            import faiss

            return faiss
        except ImportError as exc:
            raise IndexServiceError("FAISS is not installed. Install faiss-cpu.") from exc

    @staticmethod
    def _numpy() -> Any:
        try:
            import numpy as np

            return np
        except ImportError as exc:
            raise IndexServiceError("NumPy is not installed.") from exc
