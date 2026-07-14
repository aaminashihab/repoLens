from fastapi import FastAPI

from app.api.router import api_router


app = FastAPI(
    title="RepoLens API",
    version="0.1.0",
)
app.include_router(api_router)


@app.get("/", tags=["health"])
async def read_root() -> dict[str, str]:
    """Return a simple service availability response."""
    return {"message": "Hello World"}
