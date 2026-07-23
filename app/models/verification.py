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


# Generic non-technical chatbot prompt patterns
_PURE_CHATBOT_PATTERNS = (
    "what would you like to do",
    "tell me about yourself",
    "who are you",
    "explain the repo as if i'm a new engineer",
    "explain the repository as if i'm a new engineer",
    "act as",
    "imagine you are",
    "how are you",
    "what is your name",
    "hello",
    "hi there",
)

# Technical domain keywords — presence of these allows technical questions
_TECHNICAL_TERMS = {
    "auth", "middleware", "token", "redis", "query", "sql", "function", "class",
    "module", "api", "database", "method", "exception", "handler", "cache", "jwt",
    "route", "header", "encryption", "hash", "key", "password", "lock", "thread",
    "async", "event", "webhook", "config", "test", "benchmark", "validation",
    "parameter", "error", "file", "path", "symbol", "variable", "line", "service",
    "repository", "endpoint", "request", "response", "payload", "model", "schema",
    "index", "faiss", "vector", "embedding", "git", "github", "pr", "issue",
}


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

        # Reject pure non-technical meta-chatbot prompts
        for pattern in _PURE_CHATBOT_PATTERNS:
            if pattern in lower:
                raise ValueError(
                    f"'{cleaned[:60]}...' looks like a general chatbot prompt, not a verifiable code claim. "
                    "RepoLens verifies specific engineering claims (e.g., 'The rate limiter uses Redis', "
                    "'JWT tokens are validated on every authenticated route')."
                )

        # Allow technical questions that mention specific code concepts
        is_question = any(lower.startswith(q) for q in ("how does", "how do", "what is", "what are", "can you", "could you", "does the", "is the"))
        has_tech_term = any(term in lower for term in _TECHNICAL_TERMS) or any(c in cleaned for c in ("()", "_", "/", ".py", ".ts", ".js", ".go"))

        if is_question and not has_tech_term:
            raise ValueError(
                f"'{cleaned[:60]}...' is a general question without specific code terms. "
                "Specify the module, function, or technical behavior to verify."
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
