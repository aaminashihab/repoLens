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
    ttl_hours = float(os.getenv("INDEX_TTL_HOURS", "168"))
    cleanup_task = None
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


app = FastAPI(
    title="RepoLens API",
    version="0.1.0",
    lifespan=lifespan,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.include_router(api_router)
app.mount("/static", StaticFiles(directory="static"), name="static")


if not os.getenv("API_KEY"):
    logger.warning("API_KEY is not set. API key authentication is disabled.")

@app.get("/", tags=["ui"])
async def read_root() -> FileResponse:
    """Return the frontend UI."""
    return FileResponse("static/index.html")
