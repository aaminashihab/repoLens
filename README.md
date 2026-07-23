# RepoLens — Evidence-Based Repository Verification Platform

> **"Don't explain code. Verify claims about code."**

RepoLens is an **Evidence-Based Repository Verification Platform** designed to audit, test, and verify technical claims about codebases using hybrid vector search, AST call-graph traversal, multi-agent LLM-as-Judge reasoning, and automated guardrail validation.

Unlike conventional chat assistants that explain code or summarize files, RepoLens verifies concrete claims (e.g., *"Does this authentication implementation prevent privilege escalation?"*, *"Does PR #42 fix Issue #101 without regressions?"*, *"Is SQL injection possible on search endpoints?"*) and produces structured, line-level cited **Verification Reports**.

![RepoLens Architecture](static/repolens_logo.png)

[![Tests](https://img.shields.io/badge/tests-60%20passing-brightgreen)](#testing)
[![Python](https://img.shields.io/badge/python-3.12-blue)](#tech-stack)
[![FastAPI](https://img.shields.io/badge/backend-FastAPI-009688)](#tech-stack)
[![CI](https://github.com/aaminashihab/repoLens/actions/workflows/ci.yml/badge.svg)](https://github.com/aaminashihab/repoLens/actions/workflows/ci.yml)

---

## Key Features

- **Evidence-Driven Claim Verification**: Answers queries by validating claims against code snippets with zero un-cited assertions.
- **Hybrid Vector + AST Call-Graph Retrieval**: Combines FAISS vector similarity search with $N$-hop graph traversal to inspect dependent function callers and callees.
- **Multi-Agent LLM-as-Judge Pipeline**: Deconstructs user claims into testable atomic hypotheses, evaluates evidence, and outputs structured verdict reports.
- **Guardrail Validation & Refusal Framework**: Automatically downgrades verification status to `Uncertain` if evidence completeness falls below threshold or if cited code references are invalid.
- **Security Hardening**: Built-in protection against symlink attacks, path traversal, zip-bombs (50 MB total limit), oversized file denial-of-service (512 KB per-file limit), and binary file injection.
- **Input Validation & Prompt Injection Defense**: Rejects conversational chatbot prompts ("What does this code do?") and guides users to submit testable engineering claims.
- **GitHub Webhook Integration**: Automated verification triggers for Pull Requests and Issues via `POST /github/webhook`.
- **Benchmark Evaluation Framework (`RepoVerify-Bench`)**: Quantifies Precision, Recall, Hallucination Rate, Citation Accuracy, and Evidence Completeness.
- **Deterministic-First Architecture**: Uses tree-sitter AST parsing and static call-graph filtering first to minimize LLM token usage and API quota costs.

### Adversarial Auditing & Security Hardening Highlights

RepoLens underwent extensive security auditing and hardening:

- **Path Traversal Protection**: Enforced strict `resolve().relative_to(repo_root)` validation on all path operations to prevent crafted repository paths from reading host system files.
- **Symlink Defense**: `os.walk` prunes directory and file symlinks to avoid symlink-based exfiltration.
- **Stored-XSS Prevention**: DOMPurify sanitization applied at all frontend rendering boundaries (`innerHTML`) for repo metadata and LLM citations.
- **Credential Protection**: Redacts API keys and tokens from application logs and error formatters.

---

## Core Verification Architecture

```
                 POST /verify (Claim Verification Request)
                                    │
                                    ▼
       ┌────────────────────────────────────────────────────────┐
       │ 1. Input Validation & Prompt Guardrails                 │
       │    • Enforce Min Claim Length & Reject Chatbot Inputs  │
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
| **Testing** | `pytest`, **60 passing unit & integration tests** |

---

## Security Hardening & Safe Execution

RepoLens enforces strict security controls when parsing external GitHub repositories:

- **Symlink Protection**: `os.walk` prunes and skips file/directory symlinks to prevent reading system files outside the temporary directory.
- **Path Traversal Guard**: Every path is checked via `.resolve().relative_to(repo_root)` to ensure files remain strictly within the cloned root.
- **Resource Limits**: Individual files are capped at **512 KB**; total repository byte budget is capped at **50 MB**.
- **Binary Filtering**: Files containing null bytes (`b"\x00"`) are automatically skipped.
- **Atomic Storage**: Job status updates use unique temporary file descriptors (`tempfile.mkstemp`) and `os.replace` to prevent race conditions during concurrent status polling.

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

# Optional Security API Key & Webhook limits
API_KEY=                       # Gate endpoints with X-API-Key
VERIFY_RATE_LIMIT=30/minute
INDEX_RATE_LIMIT=10/hour
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
| `POST` | `/github/webhook` | Process incoming GitHub PR and Issue webhooks for automated verification |
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
      "line_range": "L17-L23",
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

---

## Testing & Quality Assurance

Run the comprehensive 60-test suite:

```bash
$env:PYTHONPATH="."; .venv\Scripts\pytest
```

```text
======================== 60 passed, 1 warning in 14.20s ========================
```

The test suite pins down:
- AST call graph node & edge construction (`test_graph.py`).
- Refusal logic on low completeness or missing supporting evidence (`test_verification.py`).
- Verification service orchestration and LLM fallback modes (`test_verification_service.py`).
- Benchmark metrics precision, recall, and hallucination rate evaluation (`test_evaluator.py`).
- `/verify` and `/github/webhook` route responses and status mappings (`test_verify_route.py`, `test_github_route.py`).

---

## License & Contributing

- **License:** MIT License ([LICENSE](LICENSE))
- **Contributing:** Guidelines available in [CONTRIBUTING.md](CONTRIBUTING.md).
