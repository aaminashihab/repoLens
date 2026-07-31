"""Tests for verification engine, guardrails, and refusal framework."""

from app.core.guardrails import GuardrailValidator
from app.models.verification import (
    EvidenceItem,
    VerificationReport,
    VerificationStatus,
)


def test_guardrail_refusal_on_low_completeness():
    report = VerificationReport(
        claim="Auth middleware prevents privilege escalation",
        verification_status=VerificationStatus.LIKELY_TRUE,
        confidence_score=95.0,
        supporting_evidence=[
            EvidenceItem(
                file_path="app/auth.py",
                line_range="L10-L20",
                symbol_name="login",
                snippet="if role != admin: raise 403",
                relevance="Checks admin role",
            )
        ],
    )

    # Validate with low completeness score (50%) -> Should trigger refusal & downgrade status to UNCERTAIN
    validated = GuardrailValidator.sanitize_and_validate(
        report=report,
        available_files={"app/auth.py"},
        completeness_score=0.50,
    )

    assert validated.verification_status == VerificationStatus.UNCERTAIN
    assert validated.confidence_score <= 49.0
    assert any("refused" in msg.lower() for msg in validated.missing_information)


def test_guardrail_refusal_on_missing_supporting_evidence():
    report = VerificationReport(
        claim="Endpoint is protected against SQL injection",
        verification_status=VerificationStatus.LIKELY_TRUE,
        confidence_score=90.0,
        supporting_evidence=[],  # Zero citations!
    )

    validated = GuardrailValidator.sanitize_and_validate(
        report=report,
        available_files={"app/db.py"},
        completeness_score=1.0,
    )

    assert validated.verification_status == VerificationStatus.UNCERTAIN
    assert any("no direct code citations" in msg.lower() for msg in validated.missing_information)


def test_guardrail_citation_stripping():
    report = VerificationReport(
        claim="Auth middleware prevents privilege escalation",
        verification_status=VerificationStatus.LIKELY_TRUE,
        confidence_score=95.0,
        supporting_evidence=[
            EvidenceItem(
                file_path="app/auth.py",
                line_range="L10-L20",
                symbol_name="login",
                snippet="if role != admin: raise 403",
                relevance="Checks admin role",
            ),
            EvidenceItem(
                file_path="app/non_existent.py",
                line_range="L5-L10",
                symbol_name="ghost",
                snippet="...",
                relevance="Invalid reference",
            )
        ],
        contradicting_evidence=[
            EvidenceItem(
                file_path="app/auth.py",
                line_range="L30-L35",
                symbol_name="bypass",
                snippet="allow_all=True",
                relevance="Bypass admin checks",
            ),
            EvidenceItem(
                file_path="app/non_existent_2.py",
                line_range="L1-L2",
                symbol_name="bypass_ghost",
                snippet="...",
                relevance="Invalid contradiction reference",
            )
        ]
    )

    # Validate with available_files limiting the citations to app/auth.py
    validated = GuardrailValidator.sanitize_and_validate(
        report=report,
        available_files={"app/auth.py"},
        completeness_score=1.0,
    )

    # The non-existent files should be stripped
    assert len(validated.supporting_evidence) == 1
    assert validated.supporting_evidence[0].file_path == "app/auth.py"

    assert len(validated.contradicting_evidence) == 1
    assert validated.contradicting_evidence[0].file_path == "app/auth.py"


def test_guardrail_no_stripping_on_empty_available_files():
    report = VerificationReport(
        claim="Auth middleware prevents privilege escalation",
        verification_status=VerificationStatus.LIKELY_TRUE,
        confidence_score=95.0,
        supporting_evidence=[
            EvidenceItem(
                file_path="app/auth.py",
                line_range="L10-L20",
                symbol_name="login",
                snippet="...",
                relevance="...",
            )
        ],
    )

    # Empty available_files means no checks/stripping is enforced
    validated = GuardrailValidator.sanitize_and_validate(
        report=report,
        available_files=set(),
        completeness_score=1.0,
    )

    assert len(validated.supporting_evidence) == 1
    assert validated.supporting_evidence[0].file_path == "app/auth.py"

