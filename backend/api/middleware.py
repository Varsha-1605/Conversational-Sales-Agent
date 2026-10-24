import logging
from uuid import uuid4

from fastapi import FastAPI, Request

from backend.api.errors import ErrorCode, error_response
from backend.logging_setup import trace_id_var

logger = logging.getLogger(__name__)

STATE_CHANGING = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def register_middleware(app: FastAPI, allowed_origin: str):
    """The request pipeline every route shares. FastAPI runs the middleware registered last first."""
    allowed = allowed_origin.rstrip("/")

    @app.middleware("http")
    async def check_origin(request: Request, call_next):
        # forgery: a state-changing request must come from our own origin (API 2). A request with no Origin is
        # refused too; browsers always send it on these methods.
        if request.method in STATE_CHANGING and (request.headers.get("origin") or "").rstrip("/") != allowed:
            logger.warning("refused %s %s: origin not allowed", request.method, request.url.path)
            return error_response(ErrorCode.FORBIDDEN)
        return await call_next(request)

    @app.middleware("http")
    async def add_trace_id(request: Request, call_next):
        # every request gets a trace ID: in every log line, the X-Trace-Id header and every error body (NF-18).
        # An unexpected exception is answered here with INTERNAL_ERROR, so no detail leaves the server.
        trace_id = uuid4().hex
        token = trace_id_var.set(trace_id)
        try:
            try:
                response = await call_next(request)
            except Exception:
                logger.exception("unexpected error on %s %s", request.method, request.url.path)
                response = error_response(ErrorCode.INTERNAL_ERROR)
            response.headers["X-Trace-Id"] = trace_id
            return response
        finally:
            trace_id_var.reset(token)
