"""Pydantic schemas for the Evidence-Based Memory/Space Efficiency Scan layer."""

from enum import Enum

from pydantic import BaseModel, Field


class MemoryVerdict(str, Enum):
    """LLM-judge verdict on a heuristic-flagged memory candidate."""

    CONFIRMED = "Confirmed"
    LIKELY = "Likely"
    FALSE_POSITIVE = "False Positive"
    UNCERTAIN = "Uncertain"


class MemoryScanRequest(BaseModel):
    """API request schema for scanning an indexed repository for memory/space issues."""

    index_id: str = Field(..., description="The FAISS / repository index ID.")
    max_findings: int = Field(
        default=15,
        ge=1,
        le=50,
        description="Maximum number of heuristic candidates to send to the LLM judge.",
    )
    min_severity: str = Field(
        default="low",
        description="Minimum heuristic severity to consider: 'low', 'medium', or 'high'.",
    )


class MemoryFinding(BaseModel):
    """A single memory/space-inefficiency finding after LLM-judge review."""

    rule_id: str = Field(..., description="Heuristic rule identifier, e.g. 'MEM-UNBOUNDED-ACCUMULATION'.")
    title: str = Field(..., description="Short human-readable title of the pattern.")
    file_path: str = Field(..., description="Relative file path in the repository.")
    line_range: str = Field(..., description="Line number range, e.g. 'L45-L62'.")
    symbol_name: str = Field(..., description="Function, class, or symbol name.")
    snippet: str = Field(..., description="Exact code snippet flagged as evidence.")
    heuristic_severity: str = Field(..., description="Severity assigned by the static heuristic: low/medium/high.")
    verdict: MemoryVerdict = Field(..., description="LLM-judge verdict on whether this is a real issue.")
    confidence_score: float = Field(..., ge=0.0, le=100.0, description="Judge confidence percentage.")
    explanation: str = Field(..., description="Why this is (or isn't) a memory/space concern.")
    suggested_fix: str | None = Field(
        default=None, description="Concrete suggestion to reduce memory/space usage."
    )


class MemoryScanReport(BaseModel):
    """The final structured memory/space efficiency scan report."""

    index_id: str = Field(..., description="The repository index that was scanned.")
    chunks_scanned: int = Field(..., description="Number of indexed code chunks scanned by heuristics.")
    candidates_found: int = Field(..., description="Number of heuristic candidates before LLM review.")
    findings: list[MemoryFinding] = Field(
        default_factory=list, description="LLM-reviewed memory/space-inefficiency findings."
    )
    overall_risk_score: float = Field(
        default=0.0, ge=0.0, le=100.0, description="Aggregate memory-risk score (0=fine, 100=severe)."
    )
    summary: str = Field(default="", description="Short natural-language summary of scan results.")
