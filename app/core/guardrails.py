"""Guardrails and refusal validation logic for evidence-driven repository verification."""

import logging
import re

from app.models.verification import (
    EvidenceItem,
    VerificationReport,
    VerificationStatus,
)

logger = logging.getLogger(__name__)

# Matches "L<int>-L<int>" or "L<int>" — the line-range format produced by the LLM judge.
_LINE_RANGE_RE = re.compile(r"^L(\d+)(?:-L(\d+))?$", re.IGNORECASE)


def _is_valid_line_range(line_range: str) -> bool:
    """Return True if *line_range* is a well-formed, logically consistent citation.

    Validates:
    - Parses as ``L<start>`` or ``L<start>-L<end>``
    - Both values are positive integers
    - start <= end (when a range is present)

    An LLM could hallucinate ``L999-L1020`` for a 50-line file; this check
    catches the most egregious format/logic errors before they reach the caller.
    We intentionally do NOT check actual file line counts here (that would
    require the chunk store), but format + ordering validation is already a
    meaningful improvement over no validation.
    """
    m = _LINE_RANGE_RE.match(line_range.strip())
    if not m:
        return False
    start = int(m.group(1))
    end = int(m.group(2)) if m.group(2) is not None else start
    return start >= 1 and end >= start


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
    ) -> VerificationReport:
        """Validate report evidence against repository reality and apply refusal guardrails.

        Rules:
        1. If evidence completeness score < MIN_COMPLETENESS_THRESHOLD (70%), downgrade status to UNCERTAIN.
        2. Ensure all citations reference valid repository file paths.
        3. Strip citations with malformed or logically impossible line ranges.
        4. If status is LIKELY_TRUE but zero supporting evidence items are cited, refuse and mark UNCERTAIN.
        """
        validated_supporting: list[EvidenceItem] = []
        validated_contradicting: list[EvidenceItem] = []

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

