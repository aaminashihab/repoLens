"""Guardrails and refusal validation logic for evidence-driven repository verification."""

import logging
from app.models.verification import (
    EvidenceItem,
    VerificationReport,
    VerificationStatus,
)

logger = logging.getLogger(__name__)


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
        3. If status is LIKELY_TRUE but zero supporting evidence items are cited, refuse and mark UNCERTAIN.
        """
        validated_supporting: list[EvidenceItem] = []
        validated_contradicting: list[EvidenceItem] = []

        for item in report.supporting_evidence:
            if item.file_path in available_files or not available_files:
                validated_supporting.append(item)
            else:
                logger.warning(f"Guardrail stripped uncited/invalid file path: {item.file_path}")

        for item in report.contradicting_evidence:
            if item.file_path in available_files or not available_files:
                validated_contradicting.append(item)

        # Rule 1: Refusal on low evidence completeness
        new_status = report.verification_status
        new_confidence = report.confidence_score
        missing_info = list(report.missing_information)

        if completeness_score < GuardrailValidator.MIN_COMPLETENESS_THRESHOLD:
            logger.info(
                f"Refusal triggered: Evidence completeness ({completeness_score:.2f}) "
                f"is below threshold ({GuardrailValidator.MIN_COMPLETENESS_THRESHOLD:.2f})."
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
