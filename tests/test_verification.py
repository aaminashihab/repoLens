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


def test_guardrail_line_range_bounds_validation():
    """Verify that citations citing lines outside the retrieved chunk bounds are stripped."""
    from app.services.retrieval_service import RetrievedChunk

    chunks = [
        RetrievedChunk(
            text="def check_permissions(user):\n    if not user.is_admin:\n        raise Forbidden()\n",
            file_path="app/auth.py",
            symbol_name="check_permissions",
            chunk_type="function",
            similarity_score=0.90,
            start_line=50,
            end_line=70,
        )
    ]

    report = VerificationReport(
        claim="Admin check raises Forbidden on non-admin user",
        verification_status=VerificationStatus.LIKELY_TRUE,
        confidence_score=95.0,
        supporting_evidence=[
            # Valid citation inside chunk bounds [50, 70]
            EvidenceItem(
                file_path="app/auth.py",
                line_range="L51-L53",
                symbol_name="check_permissions",
                snippet="if not user.is_admin: raise Forbidden()",
                relevance="Checks admin flag",
            ),
            # Out-of-bounds citation (e.g. LLM hallucinated lines 999-1005 for a file with only 50-70 retrieved)
            EvidenceItem(
                file_path="app/auth.py",
                line_range="L999-L1005",
                symbol_name="ghost_symbol",
                snippet="if not user.is_admin: raise Forbidden()",
                relevance="Hallucinated line range",
            ),
            # Line range before chunk start line
            EvidenceItem(
                file_path="app/auth.py",
                line_range="L1-L10",
                symbol_name="early_symbol",
                snippet="import os",
                relevance="Before chunk bounds",
            ),
        ],
    )

    validated = GuardrailValidator.sanitize_and_validate(
        report=report,
        available_files={"app/auth.py"},
        completeness_score=1.0,
        evidence_chunks=chunks,
    )

    # Only the citation inside [50, 70] should survive
    assert len(validated.supporting_evidence) == 1
    assert validated.supporting_evidence[0].line_range == "L51-L53"


def test_guardrail_snippet_grounding_validation():
    """Verify that citations with hallucinated snippets not matching the chunk are stripped."""
    from app.services.retrieval_service import RetrievedChunk

    chunks = [
        RetrievedChunk(
            text="def hash_password(password: str) -> str:\n    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()\n",
            file_path="app/crypto.py",
            symbol_name="hash_password",
            chunk_type="function",
            similarity_score=0.95,
            start_line=10,
            end_line=25,
        )
    ]

    report = VerificationReport(
        claim="Passwords are hashed using bcrypt",
        verification_status=VerificationStatus.LIKELY_TRUE,
        confidence_score=90.0,
        supporting_evidence=[
            # Grounded snippet that actually exists in the chunk
            EvidenceItem(
                file_path="app/crypto.py",
                line_range="L10-L15",
                symbol_name="hash_password",
                snippet="bcrypt.hashpw(password.encode(), bcrypt.gensalt())",
                relevance="Uses bcrypt hashing",
            ),
            # Hallucinated snippet that does not exist in the retrieved chunk
            EvidenceItem(
                file_path="app/crypto.py",
                line_range="L10-L15",
                symbol_name="hash_password",
                snippet="md5.hexdigest(password) # completely fabricated code",
                relevance="Fabricated snippet",
            ),
        ],
    )

    validated = GuardrailValidator.sanitize_and_validate(
        report=report,
        available_files={"app/crypto.py"},
        completeness_score=1.0,
        evidence_chunks=chunks,
    )

    # Only the grounded bcrypt snippet should be accepted
    assert len(validated.supporting_evidence) == 1
    assert "bcrypt" in validated.supporting_evidence[0].snippet


def test_guardrail_malformed_line_range_stripping():
    """Verify that citations with reversed or malformed line ranges are stripped."""
    report = VerificationReport(
        claim="Auth middleware prevents privilege escalation",
        verification_status=VerificationStatus.LIKELY_TRUE,
        confidence_score=95.0,
        supporting_evidence=[
            EvidenceItem(
                file_path="app/auth.py",
                line_range="L20-L10",  # Reversed: start > end!
                symbol_name="login",
                snippet="if role != admin: raise 403",
                relevance="Invalid range",
            ),
            EvidenceItem(
                file_path="app/auth.py",
                line_range="not-a-line-range",  # Non-parseable
                symbol_name="login",
                snippet="if role != admin: raise 403",
                relevance="Invalid format",
            ),
            EvidenceItem(
                file_path="app/auth.py",
                line_range="L10-L20",  # Valid
                symbol_name="login",
                snippet="if role != admin: raise 403",
                relevance="Valid",
            ),
        ],
    )

    validated = GuardrailValidator.sanitize_and_validate(
        report=report,
        available_files={"app/auth.py"},
        completeness_score=1.0,
    )

    assert len(validated.supporting_evidence) == 1
    assert validated.supporting_evidence[0].line_range == "L10-L20"

