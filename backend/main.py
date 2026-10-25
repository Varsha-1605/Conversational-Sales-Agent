from collections.abc import Sequence
from contextlib import asynccontextmanager
from pathlib import Path
import logging

from fastapi import APIRouter, FastAPI
from fastapi.staticfiles import StaticFiles

from backend.api.errors import register_error_handlers
from backend.api.middleware import register_middleware
from backend.api.router import api_router
from backend.auth.rate_limit import (
    REQUEST_WINDOW, REQUESTS_PER_SESSION, SIGN_IN_ADDRESS_WINDOW, SIGN_IN_FAILURES, SIGN_IN_FAILURES_PER_ADDRESS,
    SIGN_IN_WINDOW, SIGN_UP_WINDOW, SIGN_UPS_PER_ADDRESS, RateLimiter,
)
from backend.db.connect_postgres import postgres_app_engine
from backend.db.connect_sqlserver import sqlserver_engine
from backend.domain.clock import Clock
from backend.logging_setup import configure_logging
from config import PROJECT_ROOT, Settings, get_settings

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    *,
    routers: Sequence[APIRouter] = (api_router,),
    frontend_dir: Path | None = None,
    clock: Clock | None = None,
) -> FastAPI:
    """The FastAPI service (ARCHITECTURE 5.1). Run with: uvicorn backend.main:create_app --factory
    Tests pass their own clock to move time on."""
    settings = settings or get_settings()
    clock = clock or Clock()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging()
        # one pooled engine per database for the whole process; connections open on first use
        app.state.postgres = postgres_app_engine(settings=settings)
        app.state.sqlserver = sqlserver_engine(settings=settings)
        try:
            yield
        finally:
            app.state.postgres.dispose()
            app.state.sqlserver.dispose()

    app = FastAPI(
        title="Conversational Sales Agent",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    # what the sign-in and session code reads from the request (backend/api/route_access.py)
    app.state.settings = settings
    app.state.clock = clock
    app.state.sign_in_limiter = RateLimiter(SIGN_IN_FAILURES, SIGN_IN_WINDOW, clock)
    app.state.sign_in_address_limiter = RateLimiter(SIGN_IN_FAILURES_PER_ADDRESS, SIGN_IN_ADDRESS_WINDOW, clock)
    app.state.sign_up_limiter = RateLimiter(SIGN_UPS_PER_ADDRESS, SIGN_UP_WINDOW, clock)
    app.state.request_limiter = RateLimiter(REQUESTS_PER_SESSION, REQUEST_WINDOW, clock)
    register_error_handlers(app)
    register_middleware(app, settings.allowed_origin)
    for router in routers:
        app.include_router(router)
    # the pages are plain files served by the same app (ARCHITECTURE 3); mounted last so /api routes win.
    # The folder arrives with the first pages. Without it nothing is mounted, so any other path is
    # NOT_FOUND (a mount of a missing folder would fail on every request instead).
    frontend = frontend_dir or PROJECT_ROOT / "frontend"
    if frontend.is_dir():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    else:
        logger.info("no frontend folder at %s; pages are not served", frontend)
    return app
