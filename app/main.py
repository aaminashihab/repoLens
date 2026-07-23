import logging
import os
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

import asyncio
from contextlib import asynccontextmanager

from app.api.router import api_router
from app.api.dependencies import limiter

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Mark any jobs left in 'processing' from a previous server run as failed.
    # These are orphaned background tasks that will never complete.
    try:
        from app.api.dependencies import get_job_service
        from app.services.job_service import JobService
        import json
        from pathlib import Path
        job_storage = Path("storage/jobs")
        if job_storage.is_dir():
            orphaned = 0
            for job_file in job_storage.glob("*.json"):
                try:
                    data = json.loads(job_file.read_text(encoding="utf-8"))
                    if data.get("status") == "processing":
                        data["status"] = "failed"
                        data["error"] = "Server was restarted while this job was running."
                        job_file.write_text(json.dumps(data), encoding="utf-8")
                        orphaned += 1
                except Exception:
                    pass
            if orphaned:
                logger.warning(
                    "Marked orphaned 'processing' job(s) as failed on startup",
                    extra={"orphaned_count": orphaned},
                )
    except Exception:
        pass

    ttl_hours = float(os.getenv("INDEX_TTL_HOURS", "168"))
    cleanup_task = None
    # A value of 0 or negative disables index expiry entirely on both the
    # scheduled background sweep and the manual /indexes/cleanup endpoint.
    # Reference test: test_post_indexes_cleanup_disabled_when_ttl_is_zero in tests/test_features.py
    if ttl_hours > 0:
        async def sweep_loop():
            interval_min = int(os.getenv("INDEX_CLEANUP_INTERVAL_MINUTES", "60"))
            from app.api.dependencies import get_index_service, get_job_service
            from app.api.routes.repositories import run_indexes_cleanup
            
            index_service = get_index_service()
            job_service = get_job_service()
            while True:
                try:
                    run_indexes_cleanup(ttl_hours, index_service, job_service)
                except Exception as exc:
                    logger.exception("Failed to run background index cleanup sweep")
                await asyncio.sleep(interval_min * 60)
        cleanup_task = asyncio.create_task(sweep_loop())
    
    yield
    
    if cleanup_task:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass

    from app.services.embedding_service import _EXECUTOR
    _EXECUTOR.shutdown(wait=True)


from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="RepoLens API",
    version="0.1.0",
    lifespan=lifespan,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

cors_origins_raw = os.getenv("CORS_ORIGINS", "*")
cors_origins = [o.strip() for o in cors_origins_raw.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)
app.mount("/static", StaticFiles(directory="static"), name="static")


if not os.getenv("API_KEY"):
    logger.warning("API_KEY is not set. API key authentication is disabled.")

if not os.getenv("GITHUB_WEBHOOK_SECRET"):
    logger.warning("GITHUB_WEBHOOK_SECRET is not set. Webhook signature verification is disabled.")

# Warn at startup if the embedding provider key looks missing or malformed.
_provider = os.getenv("LLM_PROVIDER", "openai").lower()
if _provider == "gemini":
    _gemini_key = os.getenv("GEMINI_API_KEY", "")
    if not _gemini_key:
        logger.error(
            "GEMINI_API_KEY is not set. Embedding requests will fail. "
            "Set it in your .env file and restart the server."
        )
    elif not (_gemini_key.startswith("AIza") or _gemini_key.startswith("AQ.")):
        logger.warning(
            "GEMINI_API_KEY does not look like a valid Gemini API key "
            "(expected it to start with 'AIza' or 'AQ.'). Get a key from "
            "https://aistudio.google.com/app/apikey"
        )
else:
    _openai_key = os.getenv("OPENAI_API_KEY", "")
    if not _openai_key:
        logger.error(
            "OPENAI_API_KEY is not set. Embedding requests will fail. "
            "Set it in your .env file and restart the server."
        )
    elif not _openai_key.startswith("sk-"):
        logger.warning(
            "OPENAI_API_KEY does not look like a valid OpenAI key "
            "(expected it to start with 'sk-')."
        )

@app.get("/", tags=["ui"])
async def read_root() -> FileResponse:
    """Return the frontend UI."""
    return FileResponse("static/index.html")

@app.get("/health", tags=["ui"])
async def get_health() -> dict:
    """Return a lightweight health status of the backend."""
    return {"status": "ok"}
