"""Memory/Space Efficiency Scan Service.

Mirrors RepoLens's existing Evidence-Based Verification pipeline
(``VerificationService``) but instead of verifying a single user-supplied
claim, it proactively scans every indexed chunk for memory- and
space-wasting patterns:

    1. Load the persisted index's chunks (no re-clone needed — chunk
       ``content`` is already stored in index metadata).
    2. Run cheap static heuristics (``MemoryHeuristicEngine``) over every
       chunk to produce candidate findings.
    3. Send the top candidates to the LLM-as-Judge to confirm/refute each
       one, explain the memory impact, and suggest a fix.
    4. Guardrail: strip any finding whose file path isn't actually in the
       index, and cap the confidence of anything the judge couldn't map
       back to real evidence.
"""

import json
import logging
import os
from typing import Any

from app.core.memory_heuristics import HeuristicFinding, MemoryHeuristicEngine
from app.models.memory_scan import MemoryFinding, MemoryScanReport, MemoryVerdict
from app.services.chunk_service import CodeChunk
from app.services.index_service import IndexService, IndexServiceError

logger = logging.getLogger(__name__)

_SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2}


class MemoryScanServiceError(RuntimeError):
    """Raised when a memory/space efficiency scan pipeline fails."""


class MemoryScanIndexNotFoundError(MemoryScanServiceError):
    """Raised when the requested repository index does not exist."""


class MemoryScanService:
    """Orchestrates static heuristics + LLM-as-Judge review for memory/space efficiency."""

    _SYSTEM_PROMPT = (
        "You are a Memory & Space Efficiency Judge for source code. "
        "You receive candidate code snippets flagged by static heuristics as "
        "*possibly* wasting memory or storage (unbounded accumulation, "
        "full-file loads, unbounded caches, string concatenation in loops, "
        "missing pagination, etc.). For each candidate, decide whether it is "
        "a real concern given the visible code, explain the concrete memory/space "
        "impact, and suggest a fix. Respond ONLY with valid JSON conforming to "
        "the requested schema. Do not invent files or line numbers not given to you."
    )

    def __init__(
        self,
        index_service: IndexService | None = None,
        heuristic_engine: MemoryHeuristicEngine | None = None,
        client: Any | None = None,
        *,
        model: str | None = None,
    ) -> None:
        self._index_service = index_service or IndexService()
        self._heuristics = heuristic_engine or MemoryHeuristicEngine()
        self._client = client
        self._provider = os.getenv("LLM_PROVIDER", "openai").lower()
        if model:
            self._model = model
        else:
            if self._provider == "gemini":
                self._model = os.getenv("GEMINI_CHAT_MODEL", "gemini-2.5-flash")
            else:
                self._model = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")

    def scan(
        self,
        index_id: str,
        max_findings: int = 15,
        min_severity: str = "low",
    ) -> MemoryScanReport:
        """Run the full heuristic + LLM-judge memory/space scan for an indexed repository."""
        try:
            loaded = self._index_service.load_index(index_id)
        except IndexServiceError as exc:
            raise MemoryScanIndexNotFoundError(str(exc)) from exc

        chunks = loaded.chunks
        available_files = {chunk.file_path for chunk in chunks}

        min_rank = _SEVERITY_RANK.get(min_severity.lower(), 0)
        candidates: list[tuple[CodeChunk, HeuristicFinding]] = []
        for chunk in chunks:
            for finding in self._heuristics.scan_chunk(chunk.content):
                if _SEVERITY_RANK.get(finding.severity, 0) >= min_rank:
                    candidates.append((chunk, finding))

        # Highest severity first, so the LLM budget goes to the riskiest candidates.
        candidates.sort(key=lambda pair: _SEVERITY_RANK.get(pair[1].severity, 0), reverse=True)
        top_candidates = candidates[:max_findings]

        if not top_candidates:
            logger.info(
                "Memory scan found no heuristic candidates",
                extra={"index_id": index_id, "chunks_scanned": len(chunks)},
            )
            return MemoryScanReport(
                index_id=index_id,
                chunks_scanned=len(chunks),
                candidates_found=0,
                findings=[],
                overall_risk_score=0.0,
                summary="No memory/space-inefficiency patterns were detected by static heuristics.",
            )

        findings = self._run_llm_judge(top_candidates)

        # Guardrail: drop findings whose file path isn't actually part of this index.
        validated = [f for f in findings if f.file_path in available_files or not available_files]

        overall_risk = self._aggregate_risk(validated)
        summary = self._summarize(validated, len(chunks), len(candidates))

        logger.info(
            "Memory scan completed",
            extra={
                "index_id": index_id,
                "chunks_scanned": len(chunks),
                "candidates_found": len(candidates),
                "findings_returned": len(validated),
                "overall_risk_score": overall_risk,
            },
        )

        return MemoryScanReport(
            index_id=index_id,
            chunks_scanned=len(chunks),
            candidates_found=len(candidates),
            findings=validated,
            overall_risk_score=overall_risk,
            summary=summary,
        )

    def _run_llm_judge(
        self, candidates: list[tuple[CodeChunk, HeuristicFinding]]
    ) -> list[MemoryFinding]:
        """Send heuristic candidates to the LLM judge and parse structured verdicts."""
        formatted = self._format_candidates(candidates)

        prompt = f"""MEMORY/SPACE EFFICIENCY REVIEW REQUEST:

The following code snippets were flagged by static heuristics as POSSIBLE
memory or storage inefficiencies. Review each one using only the code shown.

CANDIDATES:
{formatted}

INSTRUCTIONS:
For EVERY candidate listed above (use the same "id" value), return a verdict.
Respond strictly in JSON matching this schema:
{{
  "findings": [
    {{
      "id": "<candidate id, exactly as given>",
      "verdict": "Confirmed" | "Likely" | "False Positive" | "Uncertain",
      "confidence_score": <number between 0 and 100>,
      "explanation": "<concrete memory/space impact, or why it's a false positive>",
      "suggested_fix": "<short concrete fix, or null if not applicable>"
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

            # Robust JSON extraction: check for markdown fences first (Gemini often wraps
            # responses in ```json blocks), then fall back to outermost { } bracket search.
            if text_content.startswith("```json"):
                text_content = text_content.split("```json", 1)[1].rsplit("```", 1)[0].strip()
            elif text_content.startswith("```"):
                text_content = text_content.split("```", 1)[1].rsplit("```", 1)[0].strip()
            else:
                json_start = text_content.find("{")
                json_end = text_content.rfind("}")
                if json_start != -1 and json_end != -1 and json_end > json_start:
                    text_content = text_content[json_start : json_end + 1]

            parsed = json.loads(text_content)
            verdicts_by_id = {
                str(v.get("id")): v for v in parsed.get("findings", []) if v.get("id") is not None
            }

            return self._build_findings(candidates, verdicts_by_id)
        except Exception as exc:
            logger.exception("LLM Memory-Efficiency Judge failed")
            # Fallback: return heuristic-only findings marked Uncertain, so a
            # transient LLM failure doesn't hide real static-analysis signal.
            return self._build_findings(candidates, {}, llm_error=str(exc))

    def _build_findings(
        self,
        candidates: list[tuple[CodeChunk, HeuristicFinding]],
        verdicts_by_id: dict[str, dict[str, Any]],
        llm_error: str | None = None,
    ) -> list[MemoryFinding]:
        results: list[MemoryFinding] = []
        for i, (chunk, finding) in enumerate(candidates, 1):
            candidate_id = f"C{i}"
            verdict_data = verdicts_by_id.get(candidate_id)
            line_no = chunk.start_line + finding.line_offset

            if verdict_data is None:
                verdict = MemoryVerdict.UNCERTAIN
                confidence = 20.0
                explanation = (
                    f"LLM judge unavailable ({llm_error}); showing raw heuristic signal only."
                    if llm_error
                    else "LLM judge did not return a verdict for this candidate; showing raw heuristic signal only."
                )
                suggested_fix = None
            else:
                status_str = str(verdict_data.get("verdict", "Uncertain")).strip().lower()
                verdict_map = {v.value.lower(): v for v in MemoryVerdict}
                verdict = verdict_map.get(status_str, MemoryVerdict.UNCERTAIN)
                raw_conf = float(verdict_data.get("confidence_score", 50.0))
                if raw_conf <= 1.0:
                    raw_conf *= 100.0
                confidence = max(0.0, min(100.0, raw_conf))
                explanation = verdict_data.get("explanation", finding.rationale)
                suggested_fix = verdict_data.get("suggested_fix")

            results.append(
                MemoryFinding(
                    rule_id=finding.rule_id,
                    title=finding.title,
                    file_path=chunk.file_path,
                    line_range=f"L{line_no}-L{line_no}",
                    symbol_name=chunk.symbol_name,
                    snippet=finding.matched_text,
                    heuristic_severity=finding.severity,
                    verdict=verdict,
                    confidence_score=confidence,
                    explanation=explanation,
                    suggested_fix=suggested_fix,
                )
            )
        return results

    @staticmethod
    def _format_candidates(candidates: list[tuple[CodeChunk, HeuristicFinding]]) -> str:
        parts = []
        for i, (chunk, finding) in enumerate(candidates, 1):
            line_no = chunk.start_line + finding.line_offset
            parts.append(
                f"--- CANDIDATE id=C{i} ---\n"
                f"Rule: {finding.rule_id} ({finding.title}, heuristic severity: {finding.severity})\n"
                f"File: {chunk.file_path}\n"
                f"Symbol: {chunk.symbol_name}\n"
                f"Flagged line: L{line_no}\n"
                f"Flagged code: {finding.matched_text}\n"
                f"Heuristic rationale: {finding.rationale}\n"
                f"Surrounding code:\n{chunk.content[:2000]}\n"
            )
        return "\n".join(parts)

    @staticmethod
    def _aggregate_risk(findings: list[MemoryFinding]) -> float:
        if not findings:
            return 0.0
        weight = {"high": 1.0, "medium": 0.6, "low": 0.3}
        verdict_multiplier = {
            MemoryVerdict.CONFIRMED: 1.0,
            MemoryVerdict.LIKELY: 0.7,
            MemoryVerdict.UNCERTAIN: 0.4,
            MemoryVerdict.FALSE_POSITIVE: 0.0,
        }
        total = 0.0
        for f in findings:
            base = weight.get(f.heuristic_severity, 0.3) * 100
            total += base * verdict_multiplier.get(f.verdict, 0.4) * (f.confidence_score / 100.0)
        return round(min(100.0, total / len(findings)), 2)

    @staticmethod
    def _summarize(findings: list[MemoryFinding], chunks_scanned: int, candidates_found: int) -> str:
        confirmed = sum(1 for f in findings if f.verdict in (MemoryVerdict.CONFIRMED, MemoryVerdict.LIKELY))
        if confirmed == 0:
            return (
                f"Scanned {chunks_scanned} chunks, {candidates_found} heuristic candidate(s); "
                "the LLM judge did not confirm any real memory/space concerns."
            )
        return (
            f"Scanned {chunks_scanned} chunks, {candidates_found} heuristic candidate(s); "
            f"{confirmed} finding(s) confirmed or likely to be real memory/space inefficiencies."
        )

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client

        if self._provider == "gemini":
            from google import genai

            api_key = os.getenv("GEMINI_API_KEY")
            if not api_key:
                raise MemoryScanServiceError("GEMINI_API_KEY environment variable is missing.")
            self._client = genai.Client(api_key=api_key)
        else:
            from openai import OpenAI

            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise MemoryScanServiceError("OPENAI_API_KEY environment variable is missing.")
            self._client = OpenAI(api_key=api_key)

        return self._client
