"""Pydantic schemas for the Evidence-Based Repository Verification Platform."""

from enum import Enum
from typing import Any
from pydantic import BaseModel, Field, field_validator


class VerificationStatus(str, Enum):
    """Possible outcomes for claim verification."""

    LIKELY_TRUE = "Likely True"
    LIKELY_FALSE = "Likely False"
    UNCERTAIN = "Uncertain"


class EvidenceItem(BaseModel):
    """A granular piece of code evidence cited from the repository."""

    file_path: str = Field(..., description="Relative file path in the repository.")
    line_range: str = Field(..., description="Line number range, e.g., 'L45-L62'.")
    symbol_name: str = Field(..., description="Function, class, or symbol name.")
    snippet: str = Field(..., description="Exact code snippet extracted as evidence.")
    relevance: str = Field(..., description="Explanation of why this snippet serves as evidence.")
    is_contradictory: bool = Field(
        default=False, description="True if this item contradicts the tested hypothesis."
    )


class AtomicHypothesis(BaseModel):
    """An individual sub-hypothesis extracted from the main claim."""

    hypothesis_id: str = Field(..., description="Unique identifier, e.g., 'H1'.")
    statement: str = Field(..., description="Testable sub-statement.")
    status: str = Field(
        ..., description="Sub-status: 'VERIFIED', 'REFUTED', or 'UNVERIFIABLE'."
    )
    supporting_evidence: list[EvidenceItem] = Field(default_factory=list)
    contradicting_evidence: list[EvidenceItem] = Field(default_factory=list)


class RecommendedTest(BaseModel):
    """A recommended automated test case to validate or regression-test the claim."""

    test_type: str = Field(..., description="e.g., 'Unit', 'Integration', 'Security'.")
    description: str = Field(..., description="Description of the test scenario.")
    suggested_code: str | None = Field(
        default=None, description="Optional skeleton test code."
    )


# Chatbot / ambiguous prompt patterns that are NOT code claims
_CHATBOT_PATTERNS = (
    "what would you like",
    "tell me about",
    "explain the",
    "explain this",
    "explain how",
    "how does",
    "how do i",
    "what is",
    "what are",
    "who are",
    "can you",
    "could you",
    "please help",
    "help me",
    "summarize",
    "describe",
    "show me",
    "list all",
    "give me",
    "what do",
    "imagine you",
    "act as",
    "as a new engineer",
    "joining the team",
)


class VerificationRequest(BaseModel):
    """API Request schema for verifying a claim against an indexed repository."""

    index_id: str = Field(..., description="The FAISS / repository index ID.")
    claim: str = Field(..., min_length=10, description="The claim or hypothesis to verify.")
    repository_url: str | None = Field(
        default=None, description="Optional GitHub repo URL for context."
    )
    pr_number: int | None = Field(
        default=None, description="Optional GitHub Pull Request number."
    )
    issue_number: int | None = Field(
        default=None, description="Optional GitHub Issue number."
    )

    @field_validator("claim")
    @classmethod
    def claim_must_be_verifiable(cls, v: str) -> str:
        cleaned = v.strip()
        if len(cleaned) < 10:
            raise ValueError(
                "Claim is too short. Provide a specific, verifiable statement about the codebase "
                "(e.g., 'Does the auth middleware prevent privilege escalation?')."
            )
        lower = cleaned.lower()
        for pattern in _CHATBOT_PATTERNS:
            if lower.startswith(pattern) or f" {pattern}" in lower[:60]:
                raise ValueError(
                    f"'{cleaned[:60]}...' looks like a general question, not a verifiable code claim. "
                    "RepoLens verifies specific code claims (e.g., 'The rate limiter uses Redis', "
                    "'JWT tokens are validated on every authenticated route')."
                )
        return cleaned


class VerificationReport(BaseModel):
    """The final structured verification report."""

    claim: str = Field(..., description="The original claim evaluated.")
    verification_status: VerificationStatus = Field(
        ..., description="Overall verification verdict."
    )
    confidence_score: float = Field(
        ..., ge=0.0, le=100.0, description="Confidence percentage (0 to 100)."
    )
    atomic_hypotheses: list[AtomicHypothesis] = Field(
        default_factory=list, description="Decomposed hypotheses tested."
    )
    supporting_evidence: list[EvidenceItem] = Field(
        default_factory=list, description="All supporting evidence items cited."
    )
    contradicting_evidence: list[EvidenceItem] = Field(
        default_factory=list, description="All contradicting evidence items cited."
    )
    potential_risks: list[str] = Field(
        default_factory=list, description="Identified security or architectural risks."
    )
    missing_information: list[str] = Field(
        default_factory=list, description="Unverifiable gaps or missing repository context."
    )
    recommended_tests: list[RecommendedTest] = Field(
        default_factory=list, description="Suggested automated tests to write."
    )
