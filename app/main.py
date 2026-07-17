from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router


app = FastAPI(
    title="RepoLens API",
    version="0.1.0",
)
app.include_router(api_router)
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/", tags=["ui"])
async def read_root() -> FileResponse:
    """Return the frontend UI."""
    return FileResponse("static/index.html")
