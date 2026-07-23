"""Multi-Agent Orchestrator Service for Evidence-Based Repository Verification."""

import json
import logging
import os
from time import perf_counter
from typing import Any

from app.core.guardrails import GuardrailValidator
from app.models.verification import (
    AtomicHypothesis,
    EvidenceItem,
    RecommendedTest,
    VerificationReport,
    VerificationStatus,
)
from app.services.retrieval_service import (
    IndexNotFoundError,
    RetrievalService,
    RetrievalServiceError,
    RetrievedChunk,
)

logger = logging.getLogger(__name__)


class VerificationServiceError(RuntimeError):
    """Raised when a claim verification pipeline fails."""


class VerificationService:
    """Orchestrates Claim Extraction, Hybrid Evidence Retrieval, LLM-as-Judge, and Guardrails."""

    _SYSTEM_PROMPT = (
        "You are an Evidence-Based Repository Verification Judge. "
        "Your sole core philosophy is: 'Don't explain code. Verify claims about code.' "
        "Evaluate the user's claim using strictly the retrieved code chunks. "
        "You must respond ONLY with valid JSON conforming to the requested schema. "
        "Every verdict must cite exact file paths and line ranges."
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
                self._model = os.getenv("GEMINI_CHAT_MODEL", "gemini-2.5-flash")
            else:
                self._model = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")

    def verify_claim(
        self,
        index_id: str,
        claim: str,
        repository_url: str | None = None,
        pr_number: int | None = None,
        issue_number: int | None = None,
    ) -> VerificationReport:
        """Execute multi-stage verification pipeline for claim against indexed repository."""
        started_at = perf_counter()
        normalized_claim = claim.strip()
        if not normalized_claim:
            raise VerificationServiceError("A claim to verify must not be empty.")

        # Stage 1 & 2: Hybrid evidence retrieval (vector + graph traversal)
        try:
            evidence_chunks = self._retrieval_service.retrieve_with_graph(
                index_id, normalized_claim, hops=2
            )
        except IndexNotFoundError:
            raise
        except RetrievalServiceError as exc:
            raise VerificationServiceError("Unable to retrieve evidence from repository.") from exc

        # Handle empty retrieval
        if not evidence_chunks:
            logger.warning(
                "Claim verification aborted: 0 evidence chunks retrieved",
                extra={"index_id": index_id, "claim": normalized_claim},
            )
            return VerificationReport(
                claim=normalized_claim,
                verification_status=VerificationStatus.UNCERTAIN,
                confidence_score=0.0,
                potential_risks=["No code chunks found in index."],
                missing_information=["Repository index is empty or query matched zero symbols."],
            )

        # BUG-9 FIX: available_files computed only after confirming evidence is non-empty
        available_files = {chunk.file_path for chunk in evidence_chunks}

        # Stage 3 & 4: LLM-as-Judge structured analysis
        raw_report = self._run_llm_judge(normalized_claim, evidence_chunks)

        # Stage 5: Guardrail validation & refusal enforcement
        # BUG-3 FIX: completeness_score reflects retrieved chunk coverage relative to TOP_K.
        # Guard: in unit tests, _retrieval_service may be a Mock; fall back to 5 safely.
        top_k_raw = getattr(self._retrieval_service, '_TOP_K', 5)
        top_k = top_k_raw if isinstance(top_k_raw, int) else 5
        completeness_score = min(1.0, len(evidence_chunks) / max(1, top_k))
        final_report = GuardrailValidator.sanitize_and_validate(
            report=raw_report,
            available_files=available_files,
            completeness_score=completeness_score,
        )

        logger.info(
            "Claim verification completed",
            extra={
                "index_id": index_id,
                "claim": normalized_claim,
                "status": final_report.verification_status.value,
                "confidence": final_report.confidence_score,
                "processing_time_seconds": perf_counter() - started_at,
            },
        )
        return final_report

    def _run_llm_judge(
        self, claim: str, evidence_chunks: list[RetrievedChunk]
    ) -> VerificationReport:
        """Invoke LLM-as-Judge with structured evidence prompt."""
        formatted_code = self._format_evidence_context(evidence_chunks)

        prompt = f"""VERIFICATION REQUEST:
Claim to verify: "{claim}"

REPOSITORIES RETRIEVED CODE EVIDENCE:
{formatted_code}

INSTRUCTIONS:
You are an evidence-driven verification judge. Evaluate the claim above against the code snippets provided.
Respond strictly in JSON matching this schema:
{{
  "verification_status": "Likely True" | "Likely False" | "Uncertain",
  "confidence_score": <number between 0 and 100>,
  "atomic_hypotheses": [
    {{
      "hypothesis_id": "H1",
      "statement": "<sub-hypothesis>",
      "status": "VERIFIED" | "REFUTED" | "UNVERIFIABLE"
    }}
  ],
  "supporting_evidence": [
    {{
      "file_path": "<exact file_path>",
      "line_range": "L<start>-L<end>",
      "symbol_name": "<symbol_name>",
      "snippet": "<relevant code snippet>",
      "relevance": "<explanation>"
    }}
  ],
  "contradicting_evidence": [],
  "potential_risks": ["<security or architectural risk>"],
  "missing_information": ["<gaps or unindexed dynamic behavior>"],
  "recommended_tests": [
    {{
      "test_type": "Integration",
      "description": "<test scenario description>",
      "suggested_code": "<optional code>"
    }}
  ]
}}
"""

        try:
            client = self._get_client()
            if self._provider == "gemini":
                full_prompt = f"System: {self._SYSTEM_PROMPT}\n\n{prompt}"
                response = client.models.generate_content(
                    model=self._model,
                    contents=full_prompt,
                )
                text_content = response.text
            else:
                response = client.chat.completions.create(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": self._SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    response_format={"type": "json_object"},
                )
                text_content = response.choices[0].message.content

            # BUG-6B FIX: Robust JSON extraction — handle prefixed conversational text
            # by finding the outermost JSON object boundaries.
            json_start = text_content.find('{')
            json_end = text_content.rfind('}')
            if json_start != -1 and json_end != -1 and json_end > json_start:
                text_content = text_content[json_start:json_end + 1]
            elif text_content.startswith("```json"):
                text_content = text_content.split("```json", 1)[1].rsplit("```", 1)[0].strip()
            elif text_content.startswith("```"):
                text_content = text_content.split("```", 1)[1].rsplit("```", 1)[0].strip()

            parsed = json.loads(text_content)

            # BUG-4 FIX: Case-insensitive enum lookup — LLM might return 'likely true', 'LIKELY FALSE', etc.
            status_str = parsed.get("verification_status", "Uncertain")
            status_enum = VerificationStatus.UNCERTAIN  # safe default
            _status_map = {s.value.lower(): s for s in VerificationStatus}
            status_enum = _status_map.get(str(status_str).lower().strip(), VerificationStatus.UNCERTAIN)

            # BUG-5 + BUG-8 FIX: Normalize and clamp confidence_score
            raw_confidence = float(parsed.get("confidence_score", 50.0))
            # If LLM returned a 0-1 fraction instead of 0-100, scale it up
            if raw_confidence <= 1.0:
                raw_confidence *= 100.0
            confidence_score = max(0.0, min(100.0, raw_confidence))

            supporting_items = [
                EvidenceItem(
                    file_path=item["file_path"],
                    line_range=item.get("line_range", "L1-L1"),
                    symbol_name=item.get("symbol_name", "unknown"),
                    snippet=item.get("snippet", ""),
                    relevance=item.get("relevance", ""),
                    is_contradictory=False,
                )
                for item in parsed.get("supporting_evidence", [])
            ]

            contradicting_items = [
                EvidenceItem(
                    file_path=item["file_path"],
                    line_range=item.get("line_range", "L1-L1"),
                    symbol_name=item.get("symbol_name", "unknown"),
                    snippet=item.get("snippet", ""),
                    relevance=item.get("relevance", ""),
                    is_contradictory=True,
                )
                for item in parsed.get("contradicting_evidence", [])
            ]

            atomic_hyps = [
                AtomicHypothesis(
                    hypothesis_id=h.get("hypothesis_id", "H1"),
                    statement=h.get("statement", ""),
                    status=h.get("status", "UNVERIFIABLE"),
                )
                for h in parsed.get("atomic_hypotheses", [])
            ]

            tests = [
                RecommendedTest(
                    test_type=t.get("test_type", "Unit"),
                    description=t.get("description", ""),
                    suggested_code=t.get("suggested_code"),
                )
                for t in parsed.get("recommended_tests", [])
            ]

            return VerificationReport(
                claim=claim,
                verification_status=status_enum,
                confidence_score=confidence_score,  # pre-normalized & clamped above
                atomic_hypotheses=atomic_hyps,
                supporting_evidence=supporting_items,
                contradicting_evidence=contradicting_items,
                potential_risks=parsed.get("potential_risks", []),
                missing_information=parsed.get("missing_information", []),
                recommended_tests=tests,
            )
        except Exception as exc:
            logger.exception("LLM Verification Judge failed", extra={"claim": claim})
            # Fallback safe report on LLM failure
            return VerificationReport(
                claim=claim,
                verification_status=VerificationStatus.UNCERTAIN,
                confidence_score=0.0,
                potential_risks=["LLM judge processing failed."],
                missing_information=[f"Pipeline error: {str(exc)}"],
            )

    @staticmethod
    def _format_evidence_context(chunks: list[RetrievedChunk]) -> str:
        parts = []
        for i, chunk in enumerate(chunks, 1):
            parts.append(
                f"--- EVIDENCE CHUNK #{i} ---\n"
                f"File: {chunk.file_path}\n"
                f"Symbol: {chunk.symbol_name} ({chunk.chunk_type})\n"
                f"Lines: L{chunk.start_line}-L{chunk.end_line}\n"
                f"Code:\n{chunk.text}\n"
            )
        return "\n".join(parts)

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client

        if self._provider == "gemini":
            from google import genai

            api_key = os.getenv("GEMINI_API_KEY")
            if not api_key:
                raise VerificationServiceError("GEMINI_API_KEY environment variable is missing.")
            self._client = genai.Client(api_key=api_key)
        else:
            from openai import OpenAI

            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise VerificationServiceError("OPENAI_API_KEY environment variable is missing.")
            self._client = OpenAI(api_key=api_key)

        return self._client
