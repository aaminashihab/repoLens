"""OpenAI-backed generation of semantic-chunk embeddings."""

import logging
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from app.services.chunk_service import CodeChunk


logger = logging.getLogger(__name__)


class EmbeddingServiceError(RuntimeError):
    """Raised when one or more chunk embeddings cannot be created."""


@dataclass(frozen=True, slots=True)
class EmbeddedChunk:
    """A semantic chunk paired with its embedding vector."""

    chunk: CodeChunk
    embedding: list[float]


class EmbeddingService:
    """Create OpenAI embeddings for source chunks in bounded batches."""

    def __init__(
        self,
        client: Any | None = None,
        *,
        model: str = "text-embedding-3-small",
        batch_size: int = 100,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be greater than zero.")
        self._client = client
        self._model = model
        self._batch_size = batch_size

    def embed_chunks(self, chunks: list[CodeChunk]) -> list[EmbeddedChunk]:
        """Embed every chunk's source content and preserve its input order."""
        if not chunks:
            logger.info(
                "Chunk embedding completed",
                extra={"chunks_embedded": 0, "embedding_time_seconds": 0.0},
            )
            return []

        started_at = perf_counter()
        embedded_chunks: list[EmbeddedChunk] = []
        client = self._get_client()
        for start in range(0, len(chunks), self._batch_size):
            batch = chunks[start : start + self._batch_size]
            try:
                response = client.embeddings.create(
                    model=self._model,
                    input=[chunk.content for chunk in batch],
                )
                vectors = [
                    item.embedding for item in sorted(response.data, key=lambda item: item.index)
                ]
            except Exception as exc:
                logger.exception(
                    "OpenAI embedding request failed",
                    extra={"batch_start": start, "batch_size": len(batch), "model": self._model},
                )
                raise EmbeddingServiceError("Unable to create chunk embeddings.") from exc

            if len(vectors) != len(batch):
                raise EmbeddingServiceError(
                    "The embeddings API returned an unexpected number of vectors."
                )
            embedded_chunks.extend(
                EmbeddedChunk(chunk=chunk, embedding=list(vector))
                for chunk, vector in zip(batch, vectors, strict=True)
            )

        logger.info(
            "Chunk embedding completed",
            extra={
                "chunks_embedded": len(embedded_chunks),
                "embedding_time_seconds": perf_counter() - started_at,
                "embedding_model": self._model,
            },
        )
        return embedded_chunks

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                from openai import OpenAI

                self._client = OpenAI()
            except Exception as exc:
                raise EmbeddingServiceError(
                    "Unable to initialize the OpenAI embeddings client. "
                    "Set OPENAI_API_KEY and install the openai package."
                ) from exc
        return self._client
