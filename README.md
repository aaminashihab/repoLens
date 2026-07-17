# 🔍 RepoLens

**RepoLens** is an AI-powered codebase semantic search and Q&A engine. 
Built with a focus on speed, concurrency, and reliability, RepoLens allows you to effortlessly index any public GitHub repository and ask deeply contextual questions about its architecture, logic, and symbols.

## ✨ Key Features
- **Semantic Code Search**: Employs vector embeddings and FAISS to understand the *meaning* of your code, not just the keywords.
- **Real-Time Streaming Responses (SSE)**: Answers stream back token-by-token (along with citations!) for an incredibly fast and polished user experience.
- **Dual LLM Provider Support**: Out-of-the-box support for both **OpenAI** and **Google Gemini** API ecosystems.
- **Asynchronous Background Processing**: Clones, chunks, and indexes massive repositories in the background without blocking the client.
- **Concurrent Embeddings**: Leverages multi-threading to parallelize network requests, slashing indexing time.
- **Safety First**: Implements hard resource limits and `try...finally` disk cleanup routines to prevent OOM errors and disk exhaustion.

## 🛠️ Tech Stack
- **Framework**: [FastAPI](https://fastapi.tiangolo.com/)
- **Vector Search**: [FAISS](https://github.com/facebookresearch/faiss) (CPU)
- **Code Parsing**: [Tree-Sitter](https://tree-sitter.github.io/tree-sitter/) (Python)
- **AI / Embeddings**: [OpenAI](https://platform.openai.com/) & [Google Gemini](https://ai.google.dev/)

---

## 🚀 Quick Start

### 1. Installation
Clone the repository and install the dependencies:
```bash
git clone https://github.com/your-username/repolens.git
cd repolens
pip install -r requirements.txt
```

### 2. Environment Variables
Create a `.env` file in the root directory to configure your AI provider. 

**To use Google Gemini:**
```env
LLM_PROVIDER=gemini
GEMINI_API_KEY=your_gemini_api_key
```

**To use OpenAI:**
```env
LLM_PROVIDER=openai
OPENAI_API_KEY=your_openai_api_key
# Optional: OPENAI_EMBEDDING_MODEL, OPENAI_CHAT_MODEL
```

### 3. Run the Server
Start the FastAPI application:
```bash
uvicorn app.main:app --reload
```
The API will be available at `http://localhost:8000`.

---

## 📖 API Usage

### Index a Repository
Kick off a background job to clone and index a GitHub repository.
```bash
curl -X POST http://localhost:8000/index-repository \
  -H "Content-Type: application/json" \
  -d '{"repo_url": "https://github.com/fastapi/fastapi"}'
```
**Response:** `{"index_id": "uuid-here", "status": "processing"}`

### Check Job Status
Poll the background job status (`processing`, `completed`, or `failed`).
```bash
curl -X GET http://localhost:8000/index-repository/<index_id>
```

### Ask a Question (Streaming)
Ask a natural language question about the codebase and receive a real-time SSE stream.
```bash
curl -N -X POST http://localhost:8000/ask/stream \
  -H "Content-Type: application/json" \
  -d '{"index_id": "<index_id>", "question": "How are API routes handled?"}'
```
**Streamed Response:**
```text
event: start
data: {"index_id": "uuid"}

event: token
data: {"text": "API "}

event: token
data: {"text": "routes "}

event: sources
data: [{"file_path": "...", "symbol_name": "...", "score": 0.89}]

event: done
data: {}
```

---

## 🗺️ Roadmap
- [ ] **Multi-language Support**: Expand Tree-Sitter to support JS/TS, Go, Java, and Rust.
- [ ] **Incremental Re-indexing**: Only re-embed files changed since the last indexed commit.
- [ ] **Frontend Dashboard**: A slick React/Next.js UI for navigating repositories and chatting.
- [ ] **Authentication**: Secure endpoints with user accounts and API keys.
