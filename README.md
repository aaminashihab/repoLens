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
- **Multi-Agent LLM-as-Judge Pipeline**: Deconstructs user claims into testable atomic hypotheses, evaluates evidence, and outputs structured verdict reports.
- **Guardrail Validation & Refusal Framework**: Automatically downgrades verification status to `Uncertain` if evidence completeness falls below threshold or if cited code references are invalid.
- **Automated GitHub Webhook Pipeline**: Full verification execution on incoming Pull Requests (`opened`, `synchronize`) and Issues (`opened`) via `POST /github/webhook`.
- **Security Hardening**: Built-in protection against symlink attacks, path traversal, zip-bombs (50 MB total limit with instant early exit), oversized file denial-of-service (512 KB per-file limit), and binary file injection.
- **Input Validation & Smart Claim Filtering**: Allows technical questions referencing code concepts while filtering out non-technical meta-chatbot prompts.
- **Benchmark Evaluation Framework (`RepoVerify-Bench`)**: Quantifies Precision, Recall, Hallucination Rate, Citation Accuracy, and Evidence Completeness.
- **Deterministic-First Architecture**: Uses tree-sitter AST parsing and static call-graph filtering first to minimize LLM token usage and API quota costs.

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

## Tech Stack

| Layer | Choice |
|---|---|
| **API Framework** | [FastAPI](https://fastapi.tiangolo.com/) |
| **AST Code Parsing** | [tree-sitter](https://tree-sitter.github.io/tree-sitter/) (Python grammar) + Multi-language fallback chunker |
| **Call Graph Engine** | In-memory Directed Graph (`RepositoryGraph`) |
| **Vector Search** | [FAISS](https://github.com/facebookresearch/faiss) (CPU, `IndexFlatL2`) |
| **LLM Reasoning & Embeddings** | OpenAI (`gpt-4o-mini`, `text-embedding-3-small`) or Google Gemini (`gemini-2.5-flash`, `text-embedding-004`) |
| **Repo Cloning** | [GitPython](https://gitpython.readthedocs.io/) in isolated temporary workspace |
| **Rate Limiting** | [slowapi](https://github.com/laurentS/slowapi) |
| **Frontend UI** | Vanilla JS + Dark Glassmorphic Verification Inspector + Syntax Highlighting |
| **Testing** | `pytest`, **65 passing unit & integration tests** |

---

## Security Hardening & Safe Execution

RepoLens enforces strict security controls across the application layer:

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
======================== 65 passed, 1 warning in 14.31s ========================
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
