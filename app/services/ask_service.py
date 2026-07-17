"""Grounded repository question answering using retrieval and Chat Completions."""

import json
import logging
import os
from collections.abc import Generator
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from app.services.retrieval_service import (
    IndexNotFoundError,
    RetrievalService,
    RetrievalServiceError,
    RetrievedChunk,
)


logger = logging.getLogger(__name__)


class AskServiceError(RuntimeError):
    """Raised when a repository question cannot be answered."""


class AskIndexNotFoundError(AskServiceError):
    """Raised when the requested repository index is unavailable."""


@dataclass(frozen=True, slots=True)
class AnswerSource:
    """A source symbol included in an answer's retrieval context."""

    file_path: str
    symbol_name: str
    score: float


@dataclass(frozen=True, slots=True)
class AskResult:
    """A generated answer paired with its supporting source symbols."""

    answer: str
    sources: list[AnswerSource]


class AskService:
    """Answer repository questions using retrieved code context only."""

    _SYSTEM_PROMPT = (
        "You are RepoLens, a codebase assistant. Answer only from the supplied "
        "repository context. If the context does not contain the answer, say so "
        "clearly. Cite relevant file paths and symbol names in your explanation."
    )

    def __init__(
        self,
        retrieval_service: RetrievalService | None = None,
        client: Any | None = None,
        *,
        model: str | None = None,
    ) -> None:
        self._retrieval_service = retrieval_service or RetrievalService()
        self._client = client
        self._provider = os.getenv("LLM_PROVIDER", "openai").lower()
        if model:
            self._model = model
        else:
            if self._provider == "gemini":
                self._model = "gemini-1.5-flash"
            else:
                self._model = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")

    def ask(self, index_id: str, question: str) -> AskResult:
        """Retrieve relevant code and generate a grounded answer for ``question``."""
        total_started_at = perf_counter()
        normalized_question = question.strip()
        if not normalized_question:
            raise AskServiceError("A question must not be empty.")

        retrieval_started_at = perf_counter()
        try:
            retrieved_chunks = self._retrieval_service.retrieve(index_id, normalized_question)
        except IndexNotFoundError as exc:
            raise AskIndexNotFoundError(f"Index '{index_id}' was not found.") from exc
        except RetrievalServiceError as exc:
            raise AskServiceError("Unable to retrieve repository context.") from exc
        retrieval_time_seconds = perf_counter() - retrieval_started_at

        generation_started_at = perf_counter()
        try:
            if self._provider == "gemini":
                prompt_content = f"System Instruction: {self._SYSTEM_PROMPT}\n\n{self._build_prompt(normalized_question, retrieved_chunks)}"
                response = self._get_client().models.generate_content(
                    model=self._model,
                    contents=prompt_content,
                )
                answer = response.text
            else:
                response = self._get_client().chat.completions.create(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": self._SYSTEM_PROMPT},
                        {"role": "user", "content": self._build_prompt(normalized_question, retrieved_chunks)},
                    ],
                )
                answer = response.choices[0].message.content
        except Exception as exc:
            logger.exception(f"{self._provider.title()} answer generation failed", extra={"index_id": index_id})
            raise AskServiceError("Unable to generate a repository answer.") from exc
        generation_time_seconds = perf_counter() - generation_started_at

        if not isinstance(answer, str) or not answer.strip():
            raise AskServiceError("The chat model returned an empty answer.")

        sources = [
            AnswerSource(
                file_path=chunk.file_path,
                symbol_name=chunk.symbol_name,
                score=chunk.similarity_score,
            )
            for chunk in retrieved_chunks
        ]
        logger.info(
            "Repository question answered",
            extra={
                "index_id": index_id,
                "retrieval_time_seconds": retrieval_time_seconds,
                "generation_time_seconds": generation_time_seconds,
                "total_request_time_seconds": perf_counter() - total_started_at,
                "source_count": len(sources),
                "chat_model": self._model,
            },
        )
        return AskResult(answer=answer.strip(), sources=sources)

    def stream_ask(self, index_id: str, question: str) -> Generator[str, None, None]:
        """Retrieve relevant code and stream a grounded answer as SSE events."""
        normalized_question = question.strip()
        if not normalized_question:
            yield f'event: error\ndata: {json.dumps({"message": "A question must not be empty."})}\n\n'
            return

        yield f'event: start\ndata: {json.dumps({"index_id": index_id})}\n\n'

        try:
            retrieved_chunks = self._retrieval_service.retrieve(index_id, normalized_question)
        except IndexNotFoundError as exc:
            msg = f"Index '{index_id}' was not found."
            yield f'event: error\ndata: {json.dumps({"message": msg})}\n\n'
            return
        except RetrievalServiceError as exc:
            yield f'event: error\ndata: {json.dumps({"message": "Unable to retrieve repository context."})}\n\n'
            return

        try:
            if self._provider == "gemini":
                prompt_content = f"System Instruction: {self._SYSTEM_PROMPT}\n\n{self._build_prompt(normalized_question, retrieved_chunks)}"
                response_stream = self._get_client().models.generate_content_stream(
                    model=self._model,
                    contents=prompt_content,
                )
                for chunk in response_stream:
                    if chunk.text:
                        yield f'event: token\ndata: {json.dumps({"text": chunk.text})}\n\n'
            else:
                response_stream = self._get_client().chat.completions.create(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": self._SYSTEM_PROMPT},
                        {"role": "user", "content": self._build_prompt(normalized_question, retrieved_chunks)},
                    ],
                    stream=True,
                )
                for chunk in response_stream:
                    content = chunk.choices[0].delta.content
                    if content:
                        yield f'event: token\ndata: {json.dumps({"text": content})}\n\n'
        except Exception as exc:
            logger.exception(f"{self._provider.title()} streaming answer generation failed", extra={"index_id": index_id})
            yield f'event: error\ndata: {json.dumps({"message": "Unable to generate a repository answer."})}\n\n'
            return

        sources = [
            {
                "file_path": chunk.file_path,
                "symbol_name": chunk.symbol_name,
                "score": chunk.similarity_score,
            }
            for chunk in retrieved_chunks
        ]
        
        yield f'event: sources\ndata: {json.dumps(sources)}\n\n'
        yield 'event: done\ndata: {}\n\n'

    @classmethod
    def _build_prompt(cls, question: str, chunks: list[RetrievedChunk]) -> str:
        """Format retrieved code and source metadata into chat context."""
        context = "\n\n".join(
            "\n".join(
                (
                    f"### Code chunk {position}",
                    f"File path: {chunk.file_path}",
                    f"Symbol name: {chunk.symbol_name}",
                    f"Chunk type: {chunk.chunk_type}",
                    "```python",
                    chunk.text,
                    "```",
                )
            )
            for position, chunk in enumerate(chunks, start=1)
        )
        if not context:
            context = "No relevant code chunks were retrieved."
        return f"Repository context:\n{context}\n\nUser question:\n{question}"

    def _get_client(self) -> Any:
        if self._client is None:
            if self._provider == "gemini":
                try:
                    from google import genai
                    self._client = genai.Client()
                except Exception as exc:
                    raise AskServiceError(
                        "Unable to initialize the Gemini client. Set GEMINI_API_KEY."
                    ) from exc
            else:
                try:
                    from openai import OpenAI
                    self._client = OpenAI()
                except Exception as exc:
                    raise AskServiceError(
                        "Unable to initialize the OpenAI client. Set OPENAI_API_KEY."
                    ) from exc
        return self._client
