"""OpenAI-backed generation of semantic-chunk embeddings."""

import concurrent.futures
import logging
import os
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from app.services.chunk_service import CodeChunk


# Uvicorn configures this logger with its console handler, ensuring that errors
# caught and translated to HTTP responses still retain their full traceback.
logger = logging.getLogger("uvicorn.error")


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
        self._provider = os.getenv("LLM_PROVIDER", "openai").lower()
        if model:
            self._model = model
        else:
            if self._provider == "gemini":
                self._model = "text-embedding-004"
            else:
                self._model = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
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

        batches = [
            (start, chunks[start : start + self._batch_size])
            for start in range(0, len(chunks), self._batch_size)
        ]

        def _embed_batch(batch_tuple: tuple[int, list[CodeChunk]]) -> list[EmbeddedChunk]:
            start_index, batch = batch_tuple
            try:
                if self._provider == "gemini":
                    response = client.models.embed_content(
                        model=self._model,
                        contents=[chunk.content for chunk in batch],
                    )
                    vectors = [item.values for item in response.embeddings]
                else:
                    response = client.embeddings.create(
                        model=self._model,
                        input=[chunk.content for chunk in batch],
                    )
                    vectors = [
                        item.embedding for item in sorted(response.data, key=lambda item: item.index)
                    ]
            except Exception as exc:
                logger.exception(
                    f"{self._provider.title()} embedding request failed",
                    extra={"batch_start": start_index, "batch_size": len(batch), "model": self._model},
                )
                raise EmbeddingServiceError("Unable to create chunk embeddings.") from exc

            if len(vectors) != len(batch):
                raise EmbeddingServiceError(
                    "The embeddings API returned an unexpected number of vectors."
                )
            return [
                EmbeddedChunk(chunk=chunk, embedding=list(vector))
                for chunk, vector in zip(batch, vectors, strict=True)
            ]

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            results = executor.map(_embed_batch, batches)
            for batch_result in results:
                embedded_chunks.extend(batch_result)

        logger.info(
            "Chunk embedding completed",
            extra={
                "chunks_embedded": len(embedded_chunks),
                "embedding_time_seconds": perf_counter() - started_at,
                "embedding_model": self._model,
            },
        )
        return embedded_chunks

    def embed_query(self, query: str) -> list[float]:
        """Create an embedding vector for one natural-language query."""
        normalized_query = query.strip()
        if not normalized_query:
            raise EmbeddingServiceError("A retrieval query must not be empty.")

        try:
            if self._provider == "gemini":
                response = self._get_client().models.embed_content(
                    model=self._model,
                    contents=normalized_query,
                )
                vectors = [response.embeddings[0].values]
            else:
                response = self._get_client().embeddings.create(
                    model=self._model,
                    input=normalized_query,
                )
                vectors = [
                    item.embedding for item in sorted(response.data, key=lambda item: item.index)
                ]
        except EmbeddingServiceError:
            raise
        except Exception as exc:
            logger.exception(f"{self._provider.title()} query embedding request failed", extra={"model": self._model})
            raise EmbeddingServiceError("Unable to create an embedding for the retrieval query.") from exc

        if len(vectors) != 1 or not vectors[0]:
            raise EmbeddingServiceError(
                "The embeddings API returned an invalid query embedding."
            )
        return list(vectors[0])

    def _get_client(self) -> Any:
        if self._client is None:
            if self._provider == "gemini":
                try:
                    from google import genai
                    self._client = genai.Client()
                except Exception as exc:
                    raise EmbeddingServiceError(
                        "Unable to initialize the Gemini client. Set GEMINI_API_KEY."
                    ) from exc
            else:
                try:
                    from openai import OpenAI
                    self._client = OpenAI()
                except Exception as exc:
                    raise EmbeddingServiceError(
                        "Unable to initialize the OpenAI client. Set OPENAI_API_KEY."
                    ) from exc
        return self._client
