"""Guardrails and refusal validation logic for evidence-driven repository verification."""

import collections
import logging
import re
from typing import Any

from app.models.verification import (
    EvidenceItem,
    VerificationReport,
    VerificationStatus,
)

logger = logging.getLogger(__name__)

# Matches "L<int>-L<int>" or "L<int>" — the line-range format produced by the LLM judge.
_LINE_RANGE_RE = re.compile(r"^L(\d+)(?:-L(\d+))?$", re.IGNORECASE)


def _parse_line_range(line_range: str) -> tuple[int, int] | None:
    """Parse a line range string into a (start, end) 1-based tuple.

    Returns None if malformed, start < 1, or end < start.
    """
    m = _LINE_RANGE_RE.match(line_range.strip())
    if not m:
        return None
    start = int(m.group(1))
    end = int(m.group(2)) if m.group(2) is not None else start
    if start < 1 or end < start:
        return None
    return start, end


def _is_valid_line_range(line_range: str) -> bool:
    """Return True if *line_range* is a well-formed, logically consistent citation."""
    return _parse_line_range(line_range) is not None


def _normalize_text(text: str) -> str:
    """Strip whitespace and lower-case text for robust substring matching."""
    return re.sub(r"\s+", " ", text.strip().lower())


def _is_citation_grounded_in_chunks(
    item: EvidenceItem, file_chunks: list[Any]
) -> bool:
    """Verify that a cited EvidenceItem matches the actual retrieved code chunks for its file.

    Validates:
    1. Line range exists and parses correctly.
    2. Cited [start, end] line range overlaps with at least one retrieved chunk's [start_line, end_line].
       An LLM citing lines 999-1005 for a chunk spanning 10-30 will be rejected.
    3. If the snippet is non-trivial (>= 8 chars and not generic ellipsis), verify it appears
       in the chunk text or shares high normalized token overlap with the chunk.
    """
    parsed = _parse_line_range(item.line_range)
    if parsed is None:
        return False
    cite_start, cite_end = parsed

    # Find chunks for this file that overlap with the cited line range
    overlapping_chunks = []
    for chunk in file_chunks:
        chunk_start = getattr(chunk, "start_line", 1)
        chunk_end = getattr(chunk, "end_line", chunk_start)
        # Check interval overlap: [cite_start, cite_end] overlaps [chunk_start, chunk_end]
        if not (cite_end < chunk_start or cite_start > chunk_end):
            overlapping_chunks.append(chunk)

    if not overlapping_chunks:
        logger.warning(
            "Guardrail rejected citation with out-of-bounds line range for retrieved chunks",
            extra={
                "file_path": item.file_path,
                "line_range": item.line_range,
                "retrieved_chunk_ranges": [
                    f"L{getattr(c, 'start_line', 1)}-L{getattr(c, 'end_line', 1)}"
                    for c in file_chunks
                ],
            },
        )
        return False

    # If snippet is non-trivial, check that it is grounded in at least one overlapping chunk
    snippet = item.snippet.strip()
    if len(snippet) >= 8 and snippet not in {"...", "/* ... */", "# ...", "pass"}:
        norm_snippet = _normalize_text(snippet)
        snippet_matched = False
        for chunk in overlapping_chunks:
            chunk_content = getattr(chunk, "text", getattr(chunk, "content", ""))
            norm_chunk = _normalize_text(chunk_content)
            if norm_snippet in norm_chunk:
                snippet_matched = True
                break
            # Token-set overlap fallback if LLM edited snippet whitespace or indentation slightly
            snippet_words = set(re.findall(r"\w+", norm_snippet))
            chunk_words = set(re.findall(r"\w+", norm_chunk))
            if snippet_words and len(snippet_words & chunk_words) / len(snippet_words) >= 0.70:
                snippet_matched = True
                break

        if not snippet_matched:
            logger.warning(
                "Guardrail rejected citation with ungrounded/hallucinated snippet",
                extra={"file_path": item.file_path, "snippet": snippet[:100]},
            )
            return False

    return True


class GuardrailValidationError(Exception):
    """Raised when a verification report violates critical safety guardrails."""


class GuardrailValidator:
    """Enforces zero unsupported claims, citation accuracy, and evidence-driven refusal."""

    MIN_COMPLETENESS_THRESHOLD: float = 0.70

    @staticmethod
    def sanitize_and_validate(
        report: VerificationReport,
        available_files: set[str],
        completeness_score: float = 1.0,
        evidence_chunks: list[Any] | None = None,
    ) -> VerificationReport:
        """Validate report evidence against repository reality and apply refusal guardrails.

        Rules:
        1. If evidence completeness score < MIN_COMPLETENESS_THRESHOLD (70%), downgrade status to UNCERTAIN.
        2. Ensure all citations reference valid repository file paths.
        3. Strip citations with malformed line ranges or ranges outside retrieved chunk bounds.
        4. Strip citations with hallucinated/ungrounded code snippets.
        5. If status is LIKELY_TRUE but zero supporting evidence items are cited, refuse and mark UNCERTAIN.
        """
        validated_supporting: list[EvidenceItem] = []
        validated_contradicting: list[EvidenceItem] = []

        chunks_by_file: dict[str, list[Any]] = collections.defaultdict(list)
        if evidence_chunks:
            for chunk in evidence_chunks:
                fpath = getattr(chunk, "file_path", None)
                if fpath:
                    chunks_by_file[fpath].append(chunk)

        for item in report.supporting_evidence:
            if item.file_path not in available_files and available_files:
                logger.warning(
                    "Guardrail stripped uncited/invalid file path",
                    extra={"file_path": item.file_path},
                )
                continue
            if not _is_valid_line_range(item.line_range):
                logger.warning(
                    "Guardrail stripped citation with malformed line range",
                    extra={"file_path": item.file_path, "line_range": item.line_range},
                )
                continue
            if evidence_chunks and item.file_path in chunks_by_file:
                if not _is_citation_grounded_in_chunks(item, chunks_by_file[item.file_path]):
                    continue
            validated_supporting.append(item)

        for item in report.contradicting_evidence:
            if item.file_path not in available_files and available_files:
                continue
            if not _is_valid_line_range(item.line_range):
                logger.warning(
                    "Guardrail stripped contradicting citation with malformed line range",
                    extra={"file_path": item.file_path, "line_range": item.line_range},
                )
                continue
            if evidence_chunks and item.file_path in chunks_by_file:
                if not _is_citation_grounded_in_chunks(item, chunks_by_file[item.file_path]):
                    continue
            validated_contradicting.append(item)

        # Rule 1: Refusal on low evidence completeness
        new_status = report.verification_status
        new_confidence = report.confidence_score
        missing_info = list(report.missing_information)

        if completeness_score < GuardrailValidator.MIN_COMPLETENESS_THRESHOLD:
            logger.info(
                "Refusal triggered: Evidence completeness below threshold",
                extra={
                    "completeness_score": completeness_score,
                    "threshold": GuardrailValidator.MIN_COMPLETENESS_THRESHOLD,
                },
            )
            new_status = VerificationStatus.UNCERTAIN
            new_confidence = min(report.confidence_score, 49.0)
            missing_info.append(
                f"Verification refused: Evidence completeness score ({completeness_score*100:.1f}%) "
                f"is below the required threshold ({GuardrailValidator.MIN_COMPLETENESS_THRESHOLD*100:.0f}%)."
            )

        # Rule 2: Zero unsupported assertions
        if new_status == VerificationStatus.LIKELY_TRUE and not validated_supporting:
            logger.warning("Refusal triggered: Verification marked LIKELY_TRUE but contains no valid supporting citations.")
            new_status = VerificationStatus.UNCERTAIN
            new_confidence = 30.0
            missing_info.append("No direct code citations were found to prove this claim.")

        return VerificationReport(
            claim=report.claim,
            verification_status=new_status,
            confidence_score=round(new_confidence, 2),
            atomic_hypotheses=report.atomic_hypotheses,
            supporting_evidence=validated_supporting,
            contradicting_evidence=validated_contradicting,
            potential_risks=report.potential_risks,
            missing_information=missing_info,
            recommended_tests=report.recommended_tests,
        )


