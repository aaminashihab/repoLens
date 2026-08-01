"""Static heuristics for detecting memory- and space-inefficient code patterns.

These rules run over already-indexed ``CodeChunk`` content (no repo re-clone
needed) and produce cheap, explainable *candidate* findings. Candidates are
then handed to the LLM-as-Judge layer (``MemoryScanService``) for
confirmation, explanation, and fix suggestions — mirroring RepoLens's
existing "hybrid retrieval -> LLM judge -> guardrails" verification
pipeline, but for memory/space efficiency instead of arbitrary claims.

Heuristics are intentionally line-oriented and regex-based rather than a
full second AST pass: they only need to flag *candidates* cheaply, since the
LLM judge stage — not this module — makes the final call.
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HeuristicFinding:
    """A candidate memory/space-inefficiency signal detected in a chunk."""

    rule_id: str
    title: str
    severity: str  # "low" | "medium" | "high"
    line_offset: int  # 0-indexed line offset within the chunk's content
    matched_text: str
    rationale: str


class MemoryHeuristicEngine:
    """Regex/line-based scanner for common memory- and space-wasting patterns."""

    # Rule catalogue: (rule_id, title, severity, compiled_pattern, rationale)
    # Note: compiled_pattern is None for MEM-UNBOUNDED-ACCUMULATION because
    # that rule is handled entirely by _match_unbounded_accumulation() with
    # look-ahead loop-body analysis; _match_simple_rules skips it (see below).
    _RULES: tuple[tuple[str, str, str, "re.Pattern[str] | None", str], ...] = (
        (
            "MEM-UNBOUNDED-ACCUMULATION",
            "Unbounded accumulation inside a loop",
            "high",
            None,  # handled by _match_unbounded_accumulation, not _match_simple_rules
            "A loop that appends/extends a collection with no visible size cap, "
            "break condition, or periodic flush can grow without bound and "
            "exhaust memory on large inputs.",
        ),
        (
            "MEM-STRING-CONCAT-LOOP",
            "String concatenation inside a loop",
            "medium",
            re.compile(r"^\s*[A-Za-z_][A-Za-z0-9_.\[\]]*\s*\+=\s*.+"),
            "Repeated `+=` on a string inside a loop reallocates and copies the "
            "whole string each iteration (O(n^2) memory churn). Prefer "
            "`''.join(...)` or a list buffer.",
        ),
        (
            "MEM-FULL-FILE-LOAD",
            "Entire file/stream read into memory",
            "medium",
            re.compile(r"\.read\(\)|\.readlines\(\)|json\.load\(|pickle\.load\("),
            "Reading a whole file/stream at once instead of iterating or "
            "streaming can spike memory proportionally to file size.",
        ),
        (
            "MEM-PANDAS-FULL-LOAD",
            "Unbounded pandas/CSV load",
            "medium",
            re.compile(r"pd\.read_csv\(|pandas\.read_csv\("),
            "Loading a CSV without `chunksize=` or column/dtype filtering "
            "materializes the entire dataset in memory at once.",
        ),
        (
            "MEM-GLOBAL-UNBOUNDED-CACHE",
            "Module/instance-level cache with no eviction",
            "high",
            re.compile(r"^\s*(_?[A-Za-z_][A-Za-z0-9_]*)\s*(:\s*[A-Za-z\[\], ]+)?\s*=\s*(\{\}|\[\]|dict\(\)|list\(\))\s*$"),
            "A module-level or long-lived dict/list initialized as a cache with "
            "no eviction (no `.pop(`, `del `, TTL, or max-size check nearby) "
            "grows for the lifetime of the process.",
        ),
        (
            "MEM-LIST-COMPREHENSION-ALL",
            "Eager materialization of a full dataset",
            "low",
            re.compile(r"return\s*\[.+for\s+\w+\s+in\s+.*(all|rows|records|results|items)\b", re.IGNORECASE),
            "Building and returning a full list comprehension over what looks "
            "like a large/unbounded source could instead be a generator to "
            "avoid holding every element in memory simultaneously.",
        ),
        (
            "MEM-NO-PAGINATION",
            "Query/fetch-all function with no pagination",
            "medium",
            re.compile(r"def\s+(get_all|list_all|fetch_all|load_all)\w*\s*\("),
            "Functions named like 'get_all'/'fetch_all' that don't accept a "
            "limit/offset/page parameter tend to load an entire table or "
            "result set into memory at once.",
        ),
    )

    # Signals that a nearby accumulation loop already guards its own growth.
    _CAP_GUARD_PATTERN = re.compile(
        r"\bbreak\b|\bif\s+len\(|\.pop\(|\bdel\s+|max_size|MAX_SIZE|maxsize|MAXSIZE|limit"
    )
    _APPEND_PATTERN = re.compile(r"\.append\(|\.extend\(|\+=\s*\[")
    _EVICTION_PATTERN = re.compile(r"\.pop\(|del\s+|TTL|ttl|expire|evict|maxsize|MAXSIZE")

    def scan_chunk(self, content: str) -> list[HeuristicFinding]:
        """Scan a single chunk's source content and return candidate findings."""
        findings: list[HeuristicFinding] = []
        lines = content.splitlines()

        for idx, line in enumerate(lines):
            findings.extend(self._match_unbounded_accumulation(lines, idx, line))
            findings.extend(self._match_simple_rules(line, idx))

        return findings

    def _match_simple_rules(self, line: str, idx: int) -> list[HeuristicFinding]:
        results: list[HeuristicFinding] = []
        for rule_id, title, severity, pattern, rationale in self._RULES:
            if rule_id == "MEM-UNBOUNDED-ACCUMULATION":
                continue  # handled separately with loop-body look-ahead
            if rule_id == "MEM-GLOBAL-UNBOUNDED-CACHE":
                # Only flag true module-level assignments (zero indentation
                # within the chunk) whose name reads like a cache/registry,
                # to avoid matching ordinary local variables inside functions.
                if line.startswith((" ", "\t")):
                    continue
                match = pattern.match(line)
                name_hint = re.search(r"cache|registry|store|memo|_seen|index", line, re.IGNORECASE)
                if match and name_hint:
                    results.append(
                        HeuristicFinding(
                            rule_id=rule_id,
                            title=title,
                            severity=severity,
                            line_offset=idx,
                            matched_text=line.strip(),
                            rationale=rationale,
                        )
                    )
                continue

            if pattern.search(line):
                results.append(
                    HeuristicFinding(
                        rule_id=rule_id,
                        title=title,
                        severity=severity,
                        line_offset=idx,
                        matched_text=line.strip(),
                        rationale=rationale,
                    )
                )
        return results

    def _match_unbounded_accumulation(
        self, lines: list[str], idx: int, line: str
    ) -> list[HeuristicFinding]:
        """Look for `for`/`while` loops whose body appends without a visible cap."""
        if not re.match(r"^\s*(for|while)\b.*:\s*$", line):
            return []

        indent = len(line) - len(line.lstrip())
        body_lines: list[str] = []
        for candidate in lines[idx + 1 : idx + 25]:
            if candidate.strip() == "":
                body_lines.append(candidate)
                continue
            candidate_indent = len(candidate) - len(candidate.lstrip())
            if candidate_indent <= indent:
                break
            body_lines.append(candidate)

        body_text = "\n".join(body_lines)
        if self._APPEND_PATTERN.search(body_text) and not self._CAP_GUARD_PATTERN.search(body_text):
            return [
                HeuristicFinding(
                    rule_id="MEM-UNBOUNDED-ACCUMULATION",
                    title="Unbounded accumulation inside a loop",
                    severity="high",
                    line_offset=idx,
                    matched_text=line.strip(),
                    rationale=(
                        "Loop body appends/extends a collection but no `break`, "
                        "`if len(...)`, size limit, or eviction was found in the "
                        "next lines of the loop body."
                    ),
                )
            ]
        return []
