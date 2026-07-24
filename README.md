# RepoLens — Evidence-Based Repository Verification Platform

> **"Don't explain code. Verify claims about code."**

RepoLens is an **Evidence-Based Repository Verification Platform** designed to audit, test, and verify technical claims about codebases using hybrid vector search, AST call-graph traversal, multi-agent LLM-as-Judge reasoning, and automated guardrail validation.

Unlike conventional chat assistants that explain code or summarize files, RepoLens verifies concrete claims (e.g., *"Does this authentication implementation prevent privilege escalation?"*, *"Does PR #42 fix Issue #101 without regressions?"*, *"Is SQL injection possible on search endpoints?"*) and produces structured, line-level cited **Verification Reports**.

![RepoLens Architecture](static/repolens_logo.png)

[![Tests](https://img.shields.io/badge/tests-65%20passing-brightgreen)](#testing--quality-assurance)
[![Python](https://img.shields.io/badge/python-3.12-blue)](#tech-stack)
[![FastAPI](https://img.shields.io/badge/backend-FastAPI-009688)](#tech-stack)
[![CI](https://github.com/aaminashihab/repoLens/actions/workflows/ci.yml/badge.svg)](https://github.com/aaminashihab/repoLens/actions/workflows/ci.yml)

---

## Key Features

- **Evidence-Driven Claim Verification**: Answers queries by validating claims against code snippets with zero un-cited assertions.
- **Hybrid Vector + AST Call-Graph Retrieval**: Combines FAISS vector similarity search with $N$-hop graph traversal to inspect dependent function callers and callees.
- **Calibrated Semantic Thresholding**: Transforms $L2$ vector distances ($\text{similarity} = \frac{1}{1 + L2}$) with a calibrated $0.15$ threshold cutoff to prune low-relevance retrieval noise ($L2 \le 5.66$).
- **Exponential Graph Hop Decay**: Graph-expanded context evidence decays with distance ($0.75 \times 0.85^{\text{depth}-1}$), giving direct 1-hop callers a higher evidence weight ($0.75$) than 2-hop callers ($0.6375$).
- **Multi-Language Chunker Engine**: Tree-sitter AST symbol parsing for Python with structured line-block chunking for JavaScript, TypeScript, Go, Rust, Java, C/C++, SQL, Shell, HTML/CSS, JSON, and YAML.
- **Multi-Agent LLM-as-Judge Pipeline**: Deconstructs user claims into testable atomic hypotheses, evaluates evidence, and outputs structured verdict reports.
- **Guardrail Validation & Refusal Framework**: Automatically downgrades verification status to `Uncertain` if evidence completeness falls below threshold or if cited code references are invalid.
- **Automated GitHub Webhook Pipeline**: Full verification execution on incoming Pull Requests (`opened`, `synchronize`) and Issues (`opened`) via `POST /github/webhook`.
- **Security Hardening**: Built-in protection against symlink attacks, path traversal, zip-bombs (50 MB total limit with instant early exit), oversized file denial-of-service (512 KB per-file limit), and binary file injection.
- **Benchmark Evaluation Framework (`RepoVerify-Bench`)**: Quantifies Precision, Recall, Hallucination Rate, Citation Accuracy, and Evidence Completeness.

---

## Core Verification Architecture

```
                 POST /verify (Claim Verification Request)
                                    │
                                    ▼
       ┌────────────────────────────────────────────────────────┐
       │ 1. Input Validation & Prompt Guardrails                 │
       │    • Enforce Min Claim Length & Technical Term Checks  │
       └────────────────────────────┬───────────────────────────┘
                                    ▼
       ┌────────────────────────────────────────────────────────┐
       │ 2. Hybrid Retrieval & Security Filter                  │
       │    • FAISS Dense Vector Similarity Search              │
       │    • Tree-sitter AST $N$-Hop Call-Graph Expansion       │
       │    • Exponential Graph Hop Decay (1-Hop: 0.75, 2-Hop: 0.6375)│
       │    • Semantic Similarity Threshold Filter (>=0.15)     │
       └────────────────────────────┬───────────────────────────┘
                                    ▼
       ┌────────────────────────────────────────────────────────┐
       │ 3. Multi-Agent LLM-as-Judge Inference                  │
       │    • Atomic Hypotheses Extraction & Testing            │
       │    • Supporting & Contradicting Evidence Citations     │
       └────────────────────────────┬───────────────────────────┘
                                    ▼
       ┌────────────────────────────────────────────────────────┐
       │ 4. Guardrail Validation & Refusal Engine               │
       │    • Citation File Path Verification                   │
       │    • Evidence Completeness Evaluation (Relative to Top-K)│
       └────────────────────────────┬───────────────────────────┘
                                    ▼
                     Structured Verification Report
                     (Status, Confidence %, Citations, Risks)
```

---

## Benchmark Metrics (`RepoVerify-Bench`)

RepoLens includes a built-in evaluation framework (`app/core/evaluator.py`) designed to measure verification precision and hallucination prevention against ground-truth technical test cases:

| Metric | Target | Evaluator Score | Description |
|---|---|---|---|
| **Precision** | $\ge 90\%$ | **100.0%** | Fraction of verified claims that match ground-truth verdicts |
| **Recall** | $\ge 90\%$ | **100.0%** | Fraction of expected ground-truth evidence files correctly retrieved |
| **Hallucination Rate** | $0.0\%$ | **0.0%** | Uncited or invalid file assertions generated by LLM reasoning |
| **Citation Accuracy** | $100\%$ | **100.0%** | Fraction of cited line snippets matching actual codebase paths |

*Note: Benchmark scores reflect empirical evaluation output from running `evaluator.py` against synthetic ground-truth claim suites.*

---

## Tech Stack

| Layer | Choice |
|---|---|
| **API Framework** | [FastAPI](https://fastapi.tiangolo.com/) |
| **AST & Code Parsing** | [tree-sitter](https://tree-sitter.github.io/tree-sitter/) (Python AST grammar) + Multi-language block fallback chunker |
| **Call Graph Engine** | In-memory Directed Graph (`RepositoryGraph`) with $N$-hop depth decay |
| **Vector Search** | [FAISS](https://github.com/facebookresearch/faiss) (CPU, `IndexFlatL2`) |
| **LLM Reasoning & Embeddings** | OpenAI (`gpt-4o-mini`, `text-embedding-3-small`) or Google Gemini (`gemini-2.5-flash`, `text-embedding-004`) |
| **Repo Cloning** | [GitPython](https://gitpython.readthedocs.io/) in isolated temporary workspace |
| **Rate Limiting** | [slowapi](https://github.com/laurentS/slowapi) |
| **Frontend UI** | Vanilla JS + Dark Glassmorphic Verification Inspector + Syntax Highlighting |
| **Testing** | `pytest`, **65 passing unit & integration tests** |

---

## Security Hardening & Safe Execution

RepoLens enforces strict security controls across the application layer:

- **Atomic Job Storage**: Job status writes use `tempfile.mkstemp` and `os.replace` to prevent race conditions during concurrent status polling.
- **Webhook HMAC Authentication**: `POST /github/webhook` verifies incoming payload signatures against `GITHUB_WEBHOOK_SECRET` using `hmac.compare_digest` with HMAC-SHA256.
- **Timing-Safe Key Comparisons**: `X-API-Key` verification uses constant-time `hmac.compare_digest` to eliminate timing side-channel leaks.
- **Credential Redaction**: Tokens and API keys are automatically stripped from Git error tracebacks and application logs before formatting.
- **Log Injection Defense**: Structured logging avoids f-string interpolation of user-controlled inputs, passing variables safely via `extra={}` fields.
- **Restrictive CORS Defaults**: CORS defaults to local origins (`localhost:8000`) with `allow_credentials=False` to prevent cross-origin credential reflection on header-gated endpoints.
- **Symlink & Path Traversal Guards**: `os.walk` prunes directory/file symlinks, and path operations enforce `.resolve().relative_to(repo_root)` validation.
- **Resource Budgeting**: Files are capped at **512 KB**; cumulative repository byte scans halt immediately at **50 MB**.

---

## Quick Start

### 1. Install Dependencies

```bash
git clone https://github.com/aaminashihab/repoLens.git
cd repoLens
python -m venv .venv
.venv\Scripts\activate   # On Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure Environment

Create a `.env` file in the project root:

```env
# Choose provider ("openai" or "gemini")
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
# OPENAI_CHAT_MODEL=gpt-4o-mini
# OPENAI_EMBEDDING_MODEL=text-embedding-3-small

# Or use Gemini
# LLM_PROVIDER=gemini
# GEMINI_API_KEY=...
# GEMINI_CHAT_MODEL=gemini-2.5-flash

# Security & Webhooks
API_KEY=                        # Gate endpoints with X-API-Key
GITHUB_WEBHOOK_SECRET=          # HMAC secret for GitHub webhooks
VERIFY_RATE_LIMIT=30/minute
INDEX_RATE_LIMIT=10/hour
GITHUB_RATE_LIMIT=30/minute
```

### 3. Run Server

```bash
uvicorn app.main:app --reload
```

Open `http://localhost:8000` to view the Evidence Verification Platform UI.

---

## API Reference

| Method | Path | Description |
|---|---|---|
| `POST` | `/verify` | Execute verification pipeline for a claim against an indexed repo |
| `POST` | `/github/webhook` | Process incoming GitHub PR & Issue webhooks and run automated verification |
| `POST` | `/index-repository` | Index a public/private GitHub repository (`repo_url`) |
| `GET` | `/index-repository/{index_id}` | Poll background indexing job status |
| `DELETE` | `/index-repository/{index_id}` | Delete repository index and metadata |
| `GET` | `/indexes` | List all available repository indexes |
| `POST` | `/ask/stream` | Stream repository context & Q&A via SSE events |

### Example Verification Request

```bash
curl -X POST http://localhost:8000/verify \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
        "index_id": "example-index-id",
        "claim": "Does this authentication implementation prevent privilege escalation?"
      }'
```

### Example Verification Response

```json
{
  "claim": "Does this authentication implementation prevent privilege escalation?",
  "verification_status": "Likely True",
  "confidence_score": 92.4,
  "atomic_hypotheses": [
    {
      "hypothesis_id": "H1",
      "statement": "Middleware checks user role against required authorization before execution",
      "status": "VERIFIED"
    }
  ],
  "supporting_evidence": [
    {
      "file_path": "app/api/dependencies.py",
      "line_range": "L19-L25",
      "symbol_name": "require_api_key",
      "snippet": "async def require_api_key(x_api_key: str | None = Header(None, alias=\"X-API-Key\")) -> None:",
      "relevance": "Checks incoming API header against configured key and raises 401 on failure."
    }
  ],
  "potential_risks": [],
  "missing_information": [],
  "recommended_tests": []
}
```

### Example Automated Webhook Response (PR Opened)

```json
{
  "status": "verification_completed",
  "pr_number": "42",
  "index_id": "idx-123",
  "verification_status": "Likely True",
  "confidence_score": 95.0,
  "supporting_evidence_count": 3
}
```

---

## Production Reliability & Ops Roadmap (v2)

While the core application layer, security guardrails, and verification pipeline are fully tested and functional, deploying RepoLens to multi-instance cloud environments (GKE / Cloud Run / ECS) involves the following infrastructure steps:

1. **Distributed Vector & Job Persistence**: Transition FAISS `IndexFlatL2` and disk-based storage (`storage/jobs`) to **pgvector / Qdrant** and **Redis / Postgres** for multi-node scalability.
2. **Observability & Telemetry**: Integrate **Prometheus metrics** and **OpenTelemetry tracing** for tracking embedding API latencies, vector retrieval scores, and token consumption.
3. **LLM Resiliency & Circuit Breakers**: Implement exponential backoff retry policies and graceful degradation to lighter models during upstream LLM rate-limiting spikes.
4. **Fine-Grained GitHub App Authentication**: Replace personal access tokens with **GitHub App Installation Tokens** scoping permissions strictly to read repo contents and post PR review comments.

---

## Testing & Quality Assurance

Run the full 65-test suite:

```bash
$env:PYTHONPATH="."; .venv\Scripts\pytest
```

```text
======================== 65 passed, 1 warning in 15.59s ========================
```

The test suite covers:
- Webhook HMAC signature verification and rate limiting (`test_github_route.py`).
- AST call graph construction and $N$-hop traversal (`test_graph.py`).
- Guardrail refusal logic and completeness validation (`test_verification.py`).
- API key constant-time comparison and route authentication (`test_verify_route.py`).
- Benchmark precision, recall, and citation accuracy (`test_evaluator.py`).

---

## License & Contributing

- **License:** MIT License ([LICENSE](LICENSE))
- **Contributing:** Guidelines available in [CONTRIBUTING.md](CONTRIBUTING.md).
