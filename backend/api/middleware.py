import logging
from uuid import uuid4

from fastapi import FastAPI, Request

from backend.api.errors import ErrorCode, error_response
from backend.logging_setup import trace_id_var

logger = logging.getLogger(__name__)

STATE_CHANGING = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Browser protections on every response: no framing of any page (clickjacking), no guessing of content types, no
# referrer sent. Our pages load only their own files, so they may load nothing else (API 2).
SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}
CONTENT_SECURITY_POLICY = "default-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
# the interactive API documentation loads its viewer from a CDN and runs inline code, so it is left out of the policy
DOCUMENTATION_PATHS = frozenset({"/api/docs", "/docs/oauth2-redirect"})


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

    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        # registered last, so it is outermost and covers every answer, errors and refusals included
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        if request.url.path not in DOCUMENTATION_PATHS:
            response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"  # an answer about a person is never kept by a browser/proxy
        return response
