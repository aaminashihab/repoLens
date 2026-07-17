"""Semantic retrieval of code chunks from persistent FAISS indexes."""

import logging
from dataclasses import dataclass
from time import perf_counter

import numpy as np

from app.services.embedding_service import EmbeddingService, EmbeddingServiceError
from app.services.index_service import IndexService, IndexServiceError


logger = logging.getLogger(__name__)


class RetrievalServiceError(RuntimeError):
    """Raised when a repository index cannot be queried."""


class IndexNotFoundError(RetrievalServiceError):
    """Raised when the requested persistent repository index is unavailable."""


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    """A code chunk returned by semantic similarity search."""

    text: str
    file_path: str
    symbol_name: str
    chunk_type: str
    similarity_score: float


class RetrievalService:
    """Embed natural-language queries and retrieve the closest repository chunks."""

    _TOP_K = 5

    def __init__(
        self,
        index_service: IndexService | None = None,
        embedding_service: EmbeddingService | None = None,
    ) -> None:
        self._index_service = index_service or IndexService()
        self._embedding_service = embedding_service or EmbeddingService()

    def retrieve(self, index_id: str, query: str) -> list[RetrievedChunk]:
        """Return up to five code chunks most relevant to ``query``.

        Stored indexes use FAISS L2 distance. Returned scores are normalized to
        ``1 / (1 + distance)``, where larger values indicate closer matches.
        """
        started_at = perf_counter()
        normalized_query = query.strip()
        if not normalized_query:
            raise RetrievalServiceError("A retrieval query must not be empty.")

        try:
            loaded_index = self._index_service.load_index(index_id)
        except IndexServiceError as exc:
            if "does not exist" in str(exc).lower():
                raise IndexNotFoundError(f"Index '{index_id}' was not found.") from exc
            raise RetrievalServiceError(f"Unable to load index '{index_id}'.") from exc

        if not loaded_index.chunks:
            self._log_retrieval(index_id, started_at, 0)
            return []

        try:
            query_embedding = self._embedding_service.embed_query(normalized_query)
        except EmbeddingServiceError as exc:
            raise RetrievalServiceError("Unable to embed the retrieval query.") from exc

        query_vector = np.asarray([query_embedding], dtype=np.float32)
        if query_vector.ndim != 2 or query_vector.shape[1] != loaded_index.index.d:
            raise RetrievalServiceError(
                "Query embedding dimension does not match the repository index."
            )

        try:
            distances, indices = loaded_index.index.search(
                query_vector, min(self._TOP_K, len(loaded_index.chunks))
            )
        except Exception as exc:
            logger.exception("FAISS retrieval failed", extra={"index_id": index_id})
            raise RetrievalServiceError(f"Unable to search index '{index_id}'.") from exc

        retrieved_chunks: list[RetrievedChunk] = []
        for distance, chunk_index in zip(distances[0], indices[0], strict=True):
            if chunk_index < 0 or chunk_index >= len(loaded_index.chunks):
                continue
            chunk = loaded_index.chunks[int(chunk_index)]
            retrieved_chunks.append(
                RetrievedChunk(
                    text=chunk.content,
                    file_path=chunk.file_path,
                    symbol_name=chunk.symbol_name,
                    chunk_type=chunk.symbol_type,
                    similarity_score=1.0 / (1.0 + max(float(distance), 0.0)),
                )
            )

        self._log_retrieval(index_id, started_at, len(retrieved_chunks))
        return retrieved_chunks

    @staticmethod
    def _log_retrieval(index_id: str, started_at: float, retrieval_count: int) -> None:
        logger.info(
            "Repository retrieval completed",
            extra={
                "index_id": index_id,
                "query_time_seconds": perf_counter() - started_at,
                "retrieval_count": retrieval_count,
            },
        )
