from fastapi import APIRouter

from app.api.routes.repositories import router as repositories_router


api_router = APIRouter()
api_router.include_router(repositories_router)
