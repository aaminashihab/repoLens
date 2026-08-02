import logging
import os

from dotenv import load_dotenv

load_dotenv()

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.dependencies import limiter
from app.api.router import api_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Mark any jobs left in 'processing' from a previous server run as failed.
    # These are orphaned background tasks that will never complete.
    try:
        import json
        import os
        import tempfile
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
                        # Write atomically (mkstemp + os.replace) so a crash
                        # mid-write never leaves an empty or corrupt job file.
                        fd, tmp_path = tempfile.mkstemp(
                            dir=job_storage,
                            prefix=job_file.stem + "_",
                            suffix=".json.tmp",
                        )
                        try:
                            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                                json.dump(data, fh)
                        except Exception:
                            os.unlink(tmp_path)
                            raise
                        os.replace(tmp_path, job_file)
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
                except Exception:
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


app = FastAPI(
    title="RepoLens API",
    version="0.1.0",
    lifespan=lifespan,
)
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ---------------------------------------------------------------------------
# Security: enforce a hard cap on incoming request body size.
# A 10 MB limit prevents resource-exhaustion attacks where an attacker sends
# a multi-GB body to bloat memory on a Render free-tier instance.
# ---------------------------------------------------------------------------
_MAX_REQUEST_BODY_BYTES = int(os.getenv("MAX_REQUEST_BODY_BYTES", str(10 * 1024 * 1024)))  # 10 MB


class _RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests whose Content-Length header exceeds the configured limit."""

    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > _MAX_REQUEST_BODY_BYTES:
            return JSONResponse(
                status_code=413,
                content={"detail": "Request body too large."},
            )
        return await call_next(request)


app.add_middleware(_RequestSizeLimitMiddleware)


# ---------------------------------------------------------------------------
# Security: add defensive HTTP response headers to every response.
# These headers protect the browser-served frontend from common web attacks
# without requiring changes to the static HTML/JS files.
# ---------------------------------------------------------------------------
class _SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Inject security-related response headers on every reply."""

    _HEADERS = {
        # Prevent MIME-type sniffing (stops browsers running injected scripts
        # from JS/JSON responses served with wrong content types).
        "X-Content-Type-Options": "nosniff",
        # Deny framing to protect against clickjacking attacks.
        "X-Frame-Options": "DENY",
        # Enforce HTTPS for 1 year (only effective behind TLS, safe otherwise).
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
        # Minimal CSP: only allow same-origin resources. Tighten per-route if
        # the UI loads third-party scripts (e.g. CDN fonts).
        "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'",
        # Don't send the full Referer header to cross-origin destinations.
        "Referrer-Policy": "strict-origin-when-cross-origin",
        # Block browser features not used by RepoLens (reduces attack surface).
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    }

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        for header, value in self._HEADERS.items():
            response.headers[header] = value
        return response


app.add_middleware(_SecurityHeadersMiddleware)

cors_origins_raw = os.getenv("CORS_ORIGINS", "")
cors_origins = [o.strip() for o in cors_origins_raw.split(",") if o.strip()]

# Restrictive default: if CORS_ORIGINS is unset, limit to local origins
if not cors_origins:
    cors_origins = [
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:3000",
    ]

# Default allow_credentials to False (RepoLens uses X-API-Key header, not cookies)
allow_credentials = False
if os.getenv("CORS_ALLOW_CREDENTIALS", "false").lower() == "true":
    if "*" in cors_origins:
        logger.warning("CORS_ALLOW_CREDENTIALS=true was requested but ignored because CORS_ORIGINS contains wildcard '*'.")
    else:
        allow_credentials = True

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=allow_credentials,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
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


@app.get("/ping", tags=["ui"])
async def ping() -> dict:
    """Ultra-lightweight keep-alive endpoint for uptime monitors.

    Hit this URL every 14 minutes from UptimeRobot / BetterStack / cron-job.org
    to prevent Render free-tier cold starts.  Responds with a minimal JSON
    payload \u2014 no filesystem I/O, no authentication, no logging overhead.

    Example UptimeRobot configuration:
        Monitor Type : HTTP(s)
        URL          : https://repolens-x7b8.onrender.com/ping
        Interval     : 14 minutes
    """
    return {"pong": True}
