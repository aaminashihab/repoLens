"""Semantic and graph-augmented retrieval of code chunks from repository indexes."""

import logging
from dataclasses import dataclass
from time import perf_counter

import numpy as np

from app.services.embedding_service import EmbeddingService, EmbeddingServiceError
from app.services.index_service import IndexService, IndexServiceError, LoadedIndex


logger = logging.getLogger(__name__)


class RetrievalServiceError(RuntimeError):
    """Raised when a repository index cannot be queried."""


class IndexNotFoundError(RetrievalServiceError):
    """Raised when the requested persistent repository index is unavailable."""


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    """A code chunk returned by similarity search or graph traversal."""

    text: str
    file_path: str
    symbol_name: str
    chunk_type: str
    similarity_score: float
    start_line: int = 1
    end_line: int = 1


class RetrievalService:
    """Embed queries and retrieve code chunks via hybrid vector FAISS + graph traversal."""

    _TOP_K = 5
    # Minimum semantic similarity score to include a chunk in evidence.
    # similarity = 1 / (1 + L2_distance); score < threshold means the chunk is too dissimilar.
    _MIN_SIMILARITY = 0.15

    def __init__(
        self,
        index_service: IndexService | None = None,
        embedding_service: EmbeddingService | None = None,
    ) -> None:
        self._index_service = index_service or IndexService()
        self._embedding_service = embedding_service or EmbeddingService()

    def retrieve(self, index_id: str, query: str) -> list[RetrievedChunk]:
        """Return top code chunks most relevant to ``query`` via FAISS vector search."""
        return self.retrieve_with_graph(index_id, query, hops=0)

    def retrieve_with_graph(
        self, index_id: str, query: str, hops: int = 2
    ) -> list[RetrievedChunk]:
        """Return code chunks using hybrid vector search + N-hop call-graph expansion."""
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
            self._log_retrieval(index_id, normalized_query, started_at, 0, total_indexed_chunks=0)
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
        seed_node_ids: list[str] = []
        seen_keys: set[tuple[str, str]] = set()

        for distance, chunk_index in zip(distances[0], indices[0], strict=True):
            if chunk_index < 0 or chunk_index >= len(loaded_index.chunks):
                continue
            chunk = loaded_index.chunks[int(chunk_index)]
            sim_score = 1.0 / (1.0 + max(float(distance), 0.0))
            # BUG-NEW-1 FIX: Filter out semantically irrelevant chunks below threshold
            if sim_score < self._MIN_SIMILARITY:
                continue
            key = (chunk.file_path, chunk.symbol_name)
            seen_keys.add(key)
            retrieved_chunks.append(
                RetrievedChunk(
                    text=chunk.content,
                    file_path=chunk.file_path,
                    symbol_name=chunk.symbol_name,
                    chunk_type=chunk.symbol_type,
                    similarity_score=sim_score,
                    start_line=chunk.start_line,
                    end_line=chunk.end_line,
                )
            )
            seed_node_ids.append(f"{chunk.file_path}::{chunk.symbol_name}")

        # Graph N-hop expansion if hops > 0
        if hops > 0 and loaded_index.graph and seed_node_ids:
            graph_nodes = loaded_index.graph.traverse_n_hops(seed_node_ids, max_depth=hops)
            for gnode in graph_nodes:
                key = (gnode.file_path, gnode.symbol_name)
                if key not in seen_keys:
                    seen_keys.add(key)
                    retrieved_chunks.append(
                        RetrievedChunk(
                            text=gnode.content,
                            file_path=gnode.file_path,
                            symbol_name=gnode.symbol_name,
                            chunk_type=gnode.symbol_type,
                            similarity_score=0.75,  # Graph-connected context score
                            start_line=gnode.start_line,
                            end_line=gnode.end_line,
                        )
                    )

        self._log_retrieval(
            index_id,
            normalized_query,
            started_at,
            len(retrieved_chunks),
            vector_count=len(seed_node_ids),
            total_indexed_chunks=len(loaded_index.chunks),
        )
        return retrieved_chunks

    @staticmethod
    def _log_retrieval(
        index_id: str,
        query: str,
        started_at: float,
        retrieval_count: int,
        vector_count: int = 0,
        total_indexed_chunks: int = 0,
    ) -> None:
        logger.info(
            "Repository retrieval completed",
            extra={
                "index_id": index_id,
                "query": query,
                "total_indexed_chunks": total_indexed_chunks,
                "vector_matches": vector_count,
                "retrieved_chunks_count": retrieval_count,
                "query_time_seconds": perf_counter() - started_at,
            },
        )
