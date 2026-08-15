"""Real end-to-end retrieval ablation: Vector-Only (hops=0) vs Hybrid (hops=2).

Usage
-----
# OpenAI (default)
    set OPENAI_API_KEY=sk-...          # Windows
    export OPENAI_API_KEY=sk-...       # macOS/Linux
    PYTHONPATH=. python scripts/run_retrieval_ablation.py

# Gemini (free tier, 768-dim)
    set LLM_PROVIDER=gemini
    set GEMINI_API_KEY=...
    PYTHONPATH=. python scripts/run_retrieval_ablation.py

What it measures
----------------
For each benchmark claim the script retrieves code chunks from a REAL FAISS
index built on the repoLens app/ directory, first with no graph expansion
(hops=0) and then with 2-hop AST call-graph expansion (hops=2).

Recall per case = (expected files found) / (total expected files)

The AVERAGE row is the number suitable for the README ablation table.

The index is built once into storage/indexes/ablation-real/ and reused on
subsequent runs (delete that directory to force a rebuild).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Resolve project root so this script works with:
#   PYTHONPATH=. python scripts/run_retrieval_ablation.py
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Imports (after path fix)
# ---------------------------------------------------------------------------
from app.services.chunk_service import ChunkService
from app.services.embedding_service import EmbeddingService
from app.services.index_service import IndexService
from app.services.retrieval_service import RetrievalService

# ---------------------------------------------------------------------------
# Benchmark claims — same 10 cases as run_benchmark.py
# ---------------------------------------------------------------------------
BENCHMARK_CLAIMS: list[tuple[str, str, list[str]]] = [
    (
        "CVE-2024-001",
        "Auth middleware strictly validates JWT signature and algorithm",
        ["app/api/dependencies.py", "app/core/guardrails.py"],
    ),
    (
        "CVE-2024-002",
        "Database queries use parameterized statements to prevent SQL injection",
        ["app/services/index_service.py"],
    ),
    (
        "CVE-2024-003",
        "File upload endpoint restricts extensions and enforces 5MB size limit",
        ["app/api/routes/repositories.py"],
    ),
    (
        "CVE-2024-004",
        "Rate limiting is enforced on sensitive POST endpoints via SlowAPI",
        ["app/main.py", "app/api/dependencies.py"],
    ),
    (
        "CVE-2024-005",
        "Memory scan heuristics flag unbounded array growth in background tasks",
        ["app/core/memory_heuristics.py", "app/services/memory_scan_service.py"],
    ),
    (
        "CVE-2024-006",
        "CORS policy permits wildcards (*) for all origins in production",
        ["app/main.py"],
    ),
    (
        "CVE-2024-007",
        "Guardrail sanitizer strips ungrounded code citations from LLM output",
        ["app/core/guardrails.py"],
    ),
    (
        "CVE-2024-008",
        "AST call-graph builder indexes Python function definitions and imports",
        ["app/core/graph.py", "app/services/chunk_service.py"],
    ),
    (
        "CVE-2024-009",
        "API key verification uses constant-time comparison to prevent timing attacks",
        ["app/api/dependencies.py"],
    ),
    (
        "CVE-2024-0010",
        "FAISS index cleanup automatically removes expired indexes based on INDEX_TTL_HOURS",
        ["app/main.py", "app/services/index_service.py"],
    ),
    # ── 10 new claims ────────────────────────────────────────────────────────
    (
        "CVE-2024-011",
        "GitHub webhook endpoint verifies HMAC-SHA256 signature before processing events",
        ["app/api/routes/github.py"],
    ),
    (
        "CVE-2024-012",
        "Git clone is restricted to github.com URLs and uses shallow clone with depth=1",
        ["app/services/clone_service.py"],
    ),
    (
        "CVE-2024-013",
        "Path traversal is prevented using resolve().relative_to() on every file operation",
        ["app/core/validation.py"],
    ),
    (
        "CVE-2024-014",
        "Repository scanner skips symlinks during file discovery to prevent symlink attacks",
        ["app/services/chunk_service.py"],
    ),
    (
        "CVE-2024-015",
        "Embedding service retries with exponential backoff up to 5 times on 429 errors",
        ["app/services/embedding_service.py"],
    ),
    (
        "CVE-2024-016",
        "Verification pipeline deconstructs claims into atomic hypotheses before LLM evaluation",
        ["app/services/verification_service.py"],
    ),
    (
        "CVE-2024-017",
        "Indexing job state machine uses atomic tempfile operations to prevent data corruption",
        ["app/services/job_service.py"],
    ),
    (
        "CVE-2024-018",
        "Ask endpoint streams grounded answers to the client via Server-Sent Events",
        ["app/services/ask_service.py", "app/api/routes/ask.py"],
    ),
    (
        "CVE-2024-019",
        "Repository indexing enforces a 50MB total size cap and a 512KB per-file limit",
        ["app/services/chunk_service.py"],
    ),
    (
        "CVE-2024-020",
        "Guardrail validator forces UNCERTAIN verdict when evidence completeness is below 70%",
        ["app/core/guardrails.py", "app/services/verification_service.py"],
    ),
]

INDEX_ID = "ablation-real"
# Point at the project root so chunk paths are stored as app/api/...
# matching the expected_evidence_files in the benchmark (e.g. app/api/dependencies.py)
REPO_PATH = _PROJECT_ROOT


# ---------------------------------------------------------------------------
# Query embedding cache — avoids re-embedding the same claim on re-runs
# ---------------------------------------------------------------------------

import hashlib
import json as _json

_QUERY_CACHE_PATH = Path("storage/indexes") / INDEX_ID / "query_cache.json"


def _load_query_cache() -> dict[str, list[float]]:
    if _QUERY_CACHE_PATH.exists():
        return _json.loads(_QUERY_CACHE_PATH.read_text())
    return {}


def _save_query_cache(cache: dict[str, list[float]]) -> None:
    _QUERY_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _QUERY_CACHE_PATH.write_text(_json.dumps(cache))


def _cache_key(query: str) -> str:
    return hashlib.sha256(query.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Recall measurement
# ---------------------------------------------------------------------------

def _build_index(
    index_service: IndexService,
    embedding_service: EmbeddingService,
) -> None:
    """Chunk, embed, and persist the FAISS index. Skipped if already exists."""
    index_dir = Path("storage/indexes") / INDEX_ID
    if (index_dir / "index.faiss").exists():
        print(f"  [cache] Index already exists at {index_dir} — skipping rebuild.")
        print("  (Delete that directory to force a full rebuild.)\n")
        return

    print(f"  Chunking {REPO_PATH} with real ChunkService (Tree-sitter AST)...")
    chunk_service = ChunkService()
    chunks, graph = chunk_service.index_repository(REPO_PATH)
    print(f"  -> {len(chunks)} chunks, {len(graph.nodes)} graph nodes\n")

    print(f"  Embedding {len(chunks)} chunks via real EmbeddingService...")
    print(f"  Provider : {os.getenv('LLM_PROVIDER', 'openai')}")
    embedded = embedding_service.embed_chunks(chunks)
    dim = embedding_service.embedding_dimension
    print(f"  -> {len(embedded)} embeddings, dimension={dim}\n")

    print(f"  Building real FAISS IndexFlatL2({dim}) index...")
    index_service.build_index(
        index_id=INDEX_ID,
        embedded_chunks=embedded,
        repo_url="local:app/",
        dimension=dim,
        graph=graph,
    )
    print(f"  -> Persisted to storage/indexes/{INDEX_ID}/\n")


def _measure_recall(
    retrieval_service: RetrievalService,
    hops: int,
) -> dict[str, float]:
    """Return per-case recall dict for the given hop depth."""
    results: dict[str, float] = {}
    query_cache = _load_query_cache()
    # Patch embed_query to serve cached vectors and persist new ones
    orig_embed_query = retrieval_service._embedding_service.embed_query

    def cached_embed_query(query: str) -> list[float]:
        key = _cache_key(query)
        if key in query_cache:
            return query_cache[key]
        vec = orig_embed_query(query)
        query_cache[key] = vec
        _save_query_cache(query_cache)
        return vec

    retrieval_service._embedding_service.embed_query = cached_embed_query

    print(f"\n  --- hops={hops} ---")
    for case_id, claim, expected_files in BENCHMARK_CLAIMS:
        retrieved = retrieval_service.retrieve_with_graph(
            index_id=INDEX_ID,
            query=claim,
            hops=hops,
        )
        retrieved_files = {chunk.file_path for chunk in retrieved}
        hits = sum(1 for f in expected_files if f in retrieved_files)
        recall = hits / len(expected_files) if expected_files else 1.0
        results[case_id] = recall
        found = [f for f in expected_files if f in retrieved_files]
        missed = [f for f in expected_files if f not in retrieved_files]
        status = "HIT" if recall == 1.0 else ("PARTIAL" if recall > 0 else "MISS")
        print(f"  {case_id:<16} [{status}] "
              f"found={found or '-'}  missed={missed or '-'}")

    retrieval_service._embedding_service.embed_query = orig_embed_query
    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 72)
    print("  Real Retrieval Ablation — Vector-Only (hops=0) vs Hybrid (hops=2)")
    print("=" * 72)
    print()

    # Validate API key is present before spending any time chunking
    provider = os.getenv("LLM_PROVIDER", "openai").lower()
    if provider == "gemini":
        if not os.getenv("GEMINI_API_KEY"):
            sys.exit(
                "ERROR: GEMINI_API_KEY is not set.\n"
                "  export GEMINI_API_KEY=<your-key>  and re-run."
            )
    else:
        if not os.getenv("OPENAI_API_KEY"):
            sys.exit(
                "ERROR: OPENAI_API_KEY is not set.\n"
                "  export OPENAI_API_KEY=sk-...  and re-run."
            )

    # batch_size=20 keeps each Gemini embedding request small enough to stay
    # within free-tier QPM limits (default 100 hits the quota on batch 2).
    embedding_service = EmbeddingService(batch_size=20)
    index_service = IndexService()
    retrieval_service = RetrievalService(
        index_service=index_service,
        embedding_service=embedding_service,
    )

    # ── Step 1: build (or reuse) the real index ──────────────────────────────
    print("[ Step 1 ] Building index")
    print("-" * 72)
    _build_index(index_service, embedding_service)

    # ── Step 2: measure recall at hops=0 ────────────────────────────────────
    print("[ Step 2 ] Measuring recall — Vector-Only (hops=0)")
    print("-" * 72)
    vector_recall = _measure_recall(retrieval_service, hops=0)

    # ── Step 3: measure recall at hops=2 ────────────────────────────────────
    print("[ Step 3 ] Measuring recall — Hybrid (hops=2)")
    print("-" * 72)
    hybrid_recall = _measure_recall(retrieval_service, hops=2)

    # ── Step 4: print results table ─────────────────────────────────────────
    print()
    print("=" * 72)
    print("  Results")
    print("=" * 72)
    header = f"{'Case':<16}  {'Vector-Only':>13}  {'Hybrid (2-hop)':>14}  {'Delta':>7}"
    print(header)
    print("-" * 72)

    for case_id, _, _ in BENCHMARK_CLAIMS:
        v = vector_recall[case_id]
        h = hybrid_recall[case_id]
        delta = h - v
        sign = "+" if delta >= 0 else ""
        print(
            f"{case_id:<16}  {v * 100:>12.1f}%  {h * 100:>13.1f}%  {sign}{delta * 100:>5.1f}pp"
        )

    print("-" * 72)
    avg_v = sum(vector_recall.values()) / len(vector_recall)
    avg_h = sum(hybrid_recall.values()) / len(hybrid_recall)
    avg_delta = avg_h - avg_v
    sign = "+" if avg_delta >= 0 else ""
    print(
        f"{'AVERAGE':<16}  {avg_v * 100:>12.1f}%  {avg_h * 100:>13.1f}%  "
        f"{sign}{avg_delta * 100:>5.1f}pp"
    )
    print("=" * 72)
    print()
    print("Use the AVERAGE row to update the README ablation table.")
    print(
        f"  Vector-Only recall : {avg_v * 100:.1f}%\n"
        f"  Hybrid recall      : {avg_h * 100:.1f}%\n"
        f"  Relative gain      : {((avg_h - avg_v) / avg_v * 100) if avg_v > 0 else 0:.1f}%"
    )


if __name__ == "__main__":
    main()
