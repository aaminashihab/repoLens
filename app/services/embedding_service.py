import concurrent.futures
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from app.services.chunk_service import CodeChunk

# Only one indexing job may call the embedding API at a time.
# This prevents concurrent jobs from exhausting the per-minute rate limit.
_EMBED_LOCK = threading.Semaphore(1)

_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=5)


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
    """Create embeddings for source chunks in sequential, rate-limit-aware batches."""

    # Maximum retries per batch when the API returns 429 RESOURCE_EXHAUSTED.
    _MAX_RETRIES = 8
    # Seconds to wait between batches to stay within the free-tier quota.
    # We use a short default delay of 2.0s to process rapidly, and rely on the
    # exponential backoff handler if we actually receive a 429 rate limit.
    _INTER_BATCH_DELAY = 2.0

    def __init__(
        self,
        client: Any | None = None,
        *,
        model: str | None = None,
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
                self._model = os.getenv("GEMINI_EMBEDDING_MODEL", "text-embedding-004")
            else:
                self._model = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
        self._batch_size = batch_size

    @property
    def embedding_dimension(self) -> int:
        """Return the dimension of embedding vectors produced by the active provider/model."""
        if self._model == "text-embedding-004":
            return 768
        elif self._model == "text-embedding-3-small":
            return 1536
        if self._provider == "gemini":
            return 768
        return 1536


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

        global _EXECUTOR
        # Acquire the global lock so concurrent indexing jobs don't compete for
        # the per-minute API quota and cause each other to exhaust retries.
        with _EMBED_LOCK:
            futures = []
            for i, batch_tuple in enumerate(batches):
                try:
                    future = _EXECUTOR.submit(self._embed_batch_with_retry, batch_tuple, client)
                except RuntimeError:
                    _EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=5)
                    future = _EXECUTOR.submit(self._embed_batch_with_retry, batch_tuple, client)
                futures.append(future)

                if i < len(batches) - 1:
                    logger.info(
                        f"Batch {i + 1}/{len(batches)} submitted; "
                        f"waiting {self._INTER_BATCH_DELAY:.0f}s before submitting next batch "
                        "to respect rate limit."
                    )
                    time.sleep(self._INTER_BATCH_DELAY)

            for future in futures:
                embedded_chunks.extend(future.result())

        logger.info(
            "Chunk embedding completed",
            extra={
                "chunks_embedded": len(embedded_chunks),
                "embedding_time_seconds": perf_counter() - started_at,
                "embedding_model": self._model,
            },
        )
        return embedded_chunks

    def _embed_batch_with_retry(
        self,
        batch_tuple: tuple[int, list[CodeChunk]],
        client: Any,
    ) -> list[EmbeddedChunk]:
        """Embed one batch, retrying with backoff on 429 RESOURCE_EXHAUSTED."""
        start_index, batch = batch_tuple
        last_exc: Exception | None = None

        for attempt in range(self._MAX_RETRIES):
            try:
                return self._embed_batch_once(start_index, batch, client)
            except EmbeddingServiceError:
                raise  # vector-count mismatches etc. are not retryable
            except Exception as exc:
                last_exc = exc
                error_str = str(exc)
                if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                    # Honour the retry-delay hint from the API when present.
                    base_delay = self._parse_retry_delay(error_str, default=30.0)
                    backoff = base_delay * (1.5 ** attempt)
                    logger.warning(
                        f"Gemini rate-limit hit on batch starting at chunk {start_index}; "
                        f"retrying in {backoff:.1f}s (attempt {attempt + 1}/{self._MAX_RETRIES})",
                    )
                    time.sleep(backoff)
                else:
                    logger.exception(
                        f"{self._provider.title()} embedding request failed",
                        extra={"batch_start": start_index, "batch_size": len(batch), "model": self._model},
                    )
                    raise EmbeddingServiceError("Unable to create chunk embeddings.") from exc

        logger.error(
            f"{self._provider.title()} embedding failed after {self._MAX_RETRIES} retries "
            f"(batch_start={start_index})",
        )
        raise EmbeddingServiceError("Unable to create chunk embeddings.") from last_exc

    def _embed_batch_once(
        self,
        start_index: int,
        batch: list[CodeChunk],
        client: Any,
    ) -> list[EmbeddedChunk]:
        """Make a single embedding API call for one batch (no retry logic)."""
        if self._provider == "gemini":
            # google-genai v2.x: pass a list of plain strings to `contents`
            # to batch-embed multiple texts in a single request.
            response = client.models.embed_content(
                model=self._model,
                contents=[chunk.content for chunk in batch],
            )
            raw_embeddings = response.embeddings or []
            vectors = [item.values for item in raw_embeddings if item.values is not None]
        else:
            response = client.embeddings.create(
                model=self._model,
                input=[chunk.content for chunk in batch],
            )
            vectors = [
                item.embedding for item in sorted(response.data, key=lambda item: item.index)
            ]

        if len(vectors) != len(batch):
            raise EmbeddingServiceError(
                f"The embeddings API returned {len(vectors)} vectors for a batch of {len(batch)}."
            )
        return [
            EmbeddedChunk(chunk=chunk, embedding=list(vector))
            for chunk, vector in zip(batch, vectors, strict=True)
        ]

    @staticmethod
    def _parse_retry_delay(error_str: str, *, default: float) -> float:
        """Extract the suggested retry delay in seconds from a 429 error message."""
        match = re.search(r"retryDelay['\"]?\s*:\s*['\"]?(\d+(?:\.\d+)?)s", error_str)
        return float(match.group(1)) if match else default

    def embed_query(self, query: str) -> list[float]:
        """Create an embedding vector for one natural-language query."""
        normalized_query = query.strip()
        if not normalized_query:
            raise EmbeddingServiceError("A retrieval query must not be empty.")

        try:
            if self._provider == "gemini":
                # google-genai v2.x: pass a single string to `contents` for
                # a single-text embedding request.
                response = self._get_client().models.embed_content(
                    model=self._model,
                    contents=normalized_query,
                )
                raw_embeddings = response.embeddings or []
                vectors = [
                    item.values for item in raw_embeddings if item.values is not None
                ]
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
                    self._client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
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
