import logging
import os
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api.router import api_router
from app.api.dependencies import limiter

logger = logging.getLogger(__name__)

app = FastAPI(
    title="RepoLens API",
    version="0.1.0",
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
