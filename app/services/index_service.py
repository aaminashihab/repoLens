"""Persistent FAISS indexes for embedded repository chunks."""

import json
import logging
from dataclasses import asdict, dataclass
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

    def build_index(self, index_id: str, embedded_chunks: list[EmbeddedChunk]) -> LoadedIndex:
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
            metadata = {
                "vector_count": len(embedded_chunks),
                "dimension": index.d,
                "chunks": [asdict(item.chunk) for item in embedded_chunks],
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
