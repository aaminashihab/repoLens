#  RepoLens

**Ask questions about any GitHub repository in plain English — and get answers grounded in the actual code, with citations.**

RepoLens clones a public (or private, with a token) GitHub repository, parses it with `tree-sitter`, embeds each function/class as a vector, and answers natural-language questions using retrieval-augmented generation over your own OpenAI or Gemini key. Answers stream back token-by-token with the exact file paths and symbols they're grounded in.

![RepoLens](static/repolens_logo.png)

##  Video Demo

Watch the walkthrough on YouTube: [RepoLens Walkthrough Demo](https://youtu.be/FW6FTw5s348)


[![Tests](https://img.shields.io/badge/tests-34%20passing-brightgreen)](#testing)
[![Python](https://img.shields.io/badge/python-3.12-blue)](#tech-stack)
[![FastAPI](https://img.shields.io/badge/backend-FastAPI-009688)](#tech-stack)
[![CI](https://github.com/aaminashihab/repoLens/actions/workflows/ci.yml/badge.svg)](https://github.com/aaminashihab/repoLens/actions/workflows/ci.yml)

---

## Why this project exists

Most "chat with your codebase" demos stop at "clone repo → embed → answer." RepoLens went a step further: it's been through several rounds of adversarial code review, and that process is documented rather than hidden. A few things that came out of it:

- **A subtle namespace-package bug that silently broke the entire test suite.** An empty `fastapi/` directory sitting at the repo root shadowed the real installed `fastapi` package, causing 474 pytest collection errors that had nothing to do with the actual code. Root-caused to Python treating the empty folder as an implicit namespace package.
- **A path traversal vulnerability** in job/index status lookups, where a crafted ID could escape the intended storage directory — found, fixed, and covered with a regression test that catches both `/` and `\` separators (the fix initially missed the Windows-style backslash case on POSIX, which the test suite itself caught).
- **A stored-XSS risk** in the frontend, where LLM-generated Markdown and repo-derived file/symbol names were being written straight to `innerHTML`. Fixed with `DOMPurify` sanitization at every render boundary.
- **Frontend XSS consistency gaps** where secondary UI elements (such as repository URLs in list items and active header info) bypassed sanitization. Wrapped all remaining API-derived values in `DOMPurify` before injecting them into `innerHTML`.
- **A credential-leak check most people skip**: when private-repo cloning was added, I explicitly verified that a failed `git clone` with a token embedded in the URL doesn't leak that token into application logs — GitPython redacts it in its own error formatting, but I didn't assume that; I tested it.

Full write-up: *(link to blog post here once published)*.

---

## Features

- **Semantic code search** — vector embeddings (OpenAI or Gemini) + FAISS, so search understands what code *does*, not just keyword matches.
- **Symbol-aware chunking** — `tree-sitter` parses Python source into functions/classes/methods rather than naive line-based splitting, so retrieved context is always a complete, meaningful unit.
- **Streaming, cited answers (SSE)** — responses stream token-by-token and end with the exact file paths and symbol names the answer is grounded in.
- **Multi-turn conversation memory** — follow-up questions carry the last 12 turns of context, both server- and client-side capped so cost stays predictable.
- **Private repository support** — pass a GitHub token per-request or via `GITHUB_TOKEN`; the token is spliced into the clone URL only, excluded from API responses and job/index metadata, and never logged.
- **API key auth + per-route rate limiting** — optional `X-API-Key` gate plus configurable request limits on indexing and asking, so a public deployment can't be used to burn your LLM quota.
- **Index lifecycle management** — list, resume, delete, and auto-expire indexes on a TTL, both via a background sweep and an on-demand endpoint.
- **Background job processing** — cloning, chunking, embedding, and indexing all run as a non-blocking background task with pollable status.

---

##  Architecture

```
                POST /index-repository
                        │
                        ▼
        ┌───────────────────────────────┐
        │  1. Validate & clone (GitPython)│  →  temp dir, cleaned up after
        └───────────────┬───────────────┘
                        ▼
        ┌───────────────────────────────┐
        │  2. Chunk with tree-sitter      │  →  functions/classes as units
        └───────────────┬───────────────┘
                        ▼
        ┌───────────────────────────────┐
        │  3. Embed chunks (OpenAI/Gemini)│
        └───────────────┬───────────────┘
                        ▼
        ┌───────────────────────────────┐
        │  4. Build FAISS index + metadata│  →  storage/indexes/<id>/
        └────────────────────────────────┘

                POST /ask/stream
                        │
                        ▼
        ┌───────────────────────────────┐
        │  Embed question → FAISS search  │
        └───────────────┬───────────────┘
                        ▼
        ┌───────────────────────────────┐
        │  Grounded prompt + history      │  →  system + prior turns + context
        └───────────────┬───────────────┘
                        ▼
        ┌───────────────────────────────┐
        │  Stream answer + sources (SSE) │
        └────────────────────────────────┘
```

Each stage is an isolated, independently-testable service (`CloneService`, `ChunkService`, `EmbeddingService`, `IndexService`, `RetrievalService`, `AskService`), wired together through FastAPI dependency injection.

---

##  Tech Stack

| Layer | Choice |
|---|---|
| API framework | [FastAPI](https://fastapi.tiangolo.com/) |
| Code parsing | [tree-sitter](https://tree-sitter.github.io/tree-sitter/) (Python grammar) |
| Vector search | [FAISS](https://github.com/facebookresearch/faiss) (CPU, `IndexFlatL2`) |
| Embeddings / chat | OpenAI or Google Gemini (`LLM_PROVIDER` switch) |
| Repo access | [GitPython](https://gitpython.readthedocs.io/) |
| Rate limiting | [slowapi](https://github.com/laurentS/slowapi) |
| Frontend | Vanilla JS + `marked` + `DOMPurify` + `highlight.js` |
| Testing | `pytest`, 34 tests covering every service and route |

---

##  Quick Start

### 1. Install

```bash
git clone https://github.com/<your-username>/repolens.git
cd repolens
pip install -r requirements.txt
```

### 2. Configure

Create a `.env` file in the project root:

```env
# Choose one provider
LLM_PROVIDER=openai            # or "gemini"
OPENAI_API_KEY=sk-...
# OPENAI_EMBEDDING_MODEL=text-embedding-3-small
# OPENAI_CHAT_MODEL=gpt-4o-mini
# GEMINI_API_KEY=...

# Optional — leave unset for open local dev
API_KEY=                       # gates all endpoints with X-API-Key if set
GITHUB_TOKEN=                  # default token for private-repo cloning

# Optional — rate limits (defaults shown)
INDEX_RATE_LIMIT=10/hour
ASK_RATE_LIMIT=30/minute

# Optional — index lifecycle (defaults shown)
INDEX_TTL_HOURS=168             # 0 disables automatic + manual expiry
INDEX_CLEANUP_INTERVAL_MINUTES=60
```

### 3. Run

```bash
uvicorn app.main:app --reload
```

Open `http://localhost:8000` for the UI, or use the API directly (below).

---

##  API Reference

| Method | Path | Description |
|---|---|---|
| `POST` | `/index-repository` | Start indexing a repo (`repo_url`, optional `github_token`) — returns `index_id` immediately |
| `GET` | `/index-repository/{index_id}` | Poll job status: `processing` / `completed` / `failed` |
| `DELETE` | `/index-repository/{index_id}` | Delete an index and its job record |
| `GET` | `/indexes` | List all indexed repositories |
| `POST` | `/indexes/cleanup` | Immediately expire indexes older than `INDEX_TTL_HOURS` |
| `POST` | `/ask` | Ask a question, get a single JSON response with sources |
| `POST` | `/ask/stream` | Ask a question, get an SSE token stream + sources |

All routes accept an `X-API-Key` header if `API_KEY` is configured.

**Index a repository:**
```bash
curl -X POST http://localhost:8000/index-repository \
  -H "Content-Type: application/json" \
  -d '{"repo_url": "https://github.com/fastapi/fastapi"}'
# → {"index_id": "uuid-here", "status": "processing"}
```

**Ask a question (streaming, with conversation history):**
```bash
curl -N -X POST http://localhost:8000/ask/stream \
  -H "Content-Type: application/json" \
  -d '{
        "index_id": "<index_id>",
        "question": "How are API routes handled?",
        "history": [
          {"role": "user", "content": "What does this project do?"},
          {"role": "assistant", "content": "It indexes and answers questions about a codebase."}
        ]
      }'
```
```text
event: start
data: {"index_id": "uuid"}

event: token
data: {"text": "API "}

event: sources
data: [{"file_path": "app/main.py", "symbol_name": "app", "score": 0.89}]

event: done
data: {}
```

---

##  Testing

```bash
pytest tests/
```

34 tests cover every service (cloning, chunking, embedding, indexing, retrieval, jobs) and every route, including negative cases: path traversal attempts, invalid IDs, auth rejection, rate-limit trips, and TTL edge cases (e.g. `INDEX_TTL_HOURS=0` must disable expiry on *both* the scheduled sweep and the manual cleanup endpoint — a distinction the test suite specifically pins down after it was initially missed on one of the two code paths).

---

##  What I'd build next

- **Multi-language chunking** — `tree-sitter` grammars exist for JS/TS, Go, Rust; currently hardcoded to Python.
- **Incremental re-indexing** — diff against the last indexed commit SHA instead of re-embedding the whole repo on every update.
- **Integration tests at the route level** — current tests hit services directly plus a handful of route tests; a full `TestClient`-based suite would catch cross-cutting regressions (like the original `fastapi/` shadowing bug) even earlier.
- **A proper frontend rebuild in React** — the current vanilla JS UI works, but state (chat history, index list, active index) is manually synced across DOM updates; a component-based rewrite would make it much easier to extend.

---

##  License & Contributing

- **License:** This project is licensed under the [MIT License](LICENSE).
- **Contributing:** Contributions are welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on how to get started.
