from fastapi import APIRouter

from backend.api import auth, personalization

# Every public route lives under /api (API 1). Each phase includes its feature router here.
api_router = APIRouter(prefix="/api")
api_router.include_router(auth.router)
api_router.include_router(personalization.router)
