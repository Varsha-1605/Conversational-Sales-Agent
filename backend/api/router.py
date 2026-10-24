from fastapi import APIRouter

# Every public route lives under /api (API 1). Each phase includes its feature router here.
api_router = APIRouter(prefix="/api")
