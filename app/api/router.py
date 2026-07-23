from fastapi import APIRouter

from app.api.routes.ask import router as ask_router
from app.api.routes.repositories import router as repositories_router
from app.api.routes.github import router as github_router
from app.api.routes.verify import router as verify_router


api_router = APIRouter()
api_router.include_router(ask_router)
api_router.include_router(repositories_router)
api_router.include_router(verify_router)
api_router.include_router(github_router)


