from enum import StrEnum
import logging

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
# FastAPI's HTTPException extends Starlette's; handling Starlette's also covers the framework's own errors
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.db.connect_sqlserver import ClientDatabaseUnavailable
from backend.logging_setup import trace_id_var

logger = logging.getLogger(__name__)


class ErrorCode(StrEnum):
    """The error codes of API.md section 5 (ARCHITECTURE 9)."""

    AUTH_REQUIRED = "AUTH_REQUIRED"
    AUTH_FAILED = "AUTH_FAILED"
    ROLE_MISMATCH = "ROLE_MISMATCH"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    RATE_LIMITED = "RATE_LIMITED"
    CHANNEL_CONFIRM_REQUIRED = "CHANNEL_CONFIRM_REQUIRED"
    NOT_ACTIVE_CHANNEL = "NOT_ACTIVE_CHANNEL"
    OUT_OF_STOCK = "OUT_OF_STOCK"
    STALE_STOCK = "STALE_STOCK"
    PRICE_CHANGED = "PRICE_CHANGED"
    NOT_ELIGIBLE = "NOT_ELIGIBLE"
    SLOT_TAKEN = "SLOT_TAKEN"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    COUPON_INVALID = "COUPON_INVALID"
    PAYMENT_DECLINED = "PAYMENT_DECLINED"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    PAYMENT_PENDING = "PAYMENT_PENDING"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"  # an unexpected failure; no detail leaves the server


STATUS = {
    ErrorCode.AUTH_REQUIRED: status.HTTP_401_UNAUTHORIZED,
    ErrorCode.AUTH_FAILED: status.HTTP_401_UNAUTHORIZED,
    ErrorCode.ROLE_MISMATCH: status.HTTP_403_FORBIDDEN,
    ErrorCode.FORBIDDEN: status.HTTP_403_FORBIDDEN,
    ErrorCode.NOT_FOUND: status.HTTP_404_NOT_FOUND,
    ErrorCode.RATE_LIMITED: status.HTTP_429_TOO_MANY_REQUESTS,
    ErrorCode.CHANNEL_CONFIRM_REQUIRED: status.HTTP_409_CONFLICT,
    ErrorCode.NOT_ACTIVE_CHANNEL: status.HTTP_409_CONFLICT,
    ErrorCode.OUT_OF_STOCK: status.HTTP_409_CONFLICT,
    ErrorCode.STALE_STOCK: status.HTTP_409_CONFLICT,
    ErrorCode.PRICE_CHANGED: status.HTTP_409_CONFLICT,
    ErrorCode.NOT_ELIGIBLE: status.HTTP_409_CONFLICT,
    ErrorCode.SLOT_TAKEN: status.HTTP_409_CONFLICT,
    ErrorCode.TOKEN_EXPIRED: status.HTTP_409_CONFLICT,
    ErrorCode.VALIDATION_ERROR: status.HTTP_422_UNPROCESSABLE_CONTENT,
    ErrorCode.COUPON_INVALID: status.HTTP_422_UNPROCESSABLE_CONTENT,
    ErrorCode.PAYMENT_DECLINED: status.HTTP_402_PAYMENT_REQUIRED,
    ErrorCode.PAYMENT_FAILED: status.HTTP_402_PAYMENT_REQUIRED,
    ErrorCode.PAYMENT_PENDING: status.HTTP_202_ACCEPTED,
    ErrorCode.SERVICE_UNAVAILABLE: status.HTTP_503_SERVICE_UNAVAILABLE,
    ErrorCode.INTERNAL_ERROR: status.HTTP_500_INTERNAL_SERVER_ERROR,
}

# plain customer language (API 5); a route can give a more specific message
MESSAGES = {
    ErrorCode.AUTH_REQUIRED: "Please sign in to continue.",
    ErrorCode.AUTH_FAILED: "Email or password is incorrect.",
    ErrorCode.ROLE_MISMATCH: "This account is not registered for the role you selected. Check the role and try again.",
    ErrorCode.FORBIDDEN: "You don't have access to this.",
    ErrorCode.NOT_FOUND: "We couldn't find that.",
    ErrorCode.RATE_LIMITED: "Too many attempts. Try again in a few minutes.",
    ErrorCode.CHANNEL_CONFIRM_REQUIRED: "Your journey is open on another channel. Continue here?",
    ErrorCode.NOT_ACTIVE_CHANNEL: "Your journey continued on another channel, so nothing was changed here.",
    ErrorCode.OUT_OF_STOCK: "That item is out of stock.",
    ErrorCode.STALE_STOCK: "Availability not confirmed for that item.",
    ErrorCode.PRICE_CHANGED: "The price has changed. Please review the new total.",
    ErrorCode.NOT_ELIGIBLE: "That action isn't available for this order.",
    ErrorCode.SLOT_TAKEN: "That slot was just taken. Here are the next ones.",
    ErrorCode.TOKEN_EXPIRED: "This confirmation has expired. Please try again.",
    ErrorCode.VALIDATION_ERROR: "Some details need fixing.",
    ErrorCode.COUPON_INVALID: "That coupon can't be used.",
    ErrorCode.PAYMENT_DECLINED: "Your payment was declined. You have not been charged.",
    ErrorCode.PAYMENT_FAILED: "The payment didn't go through. You have not been charged.",
    ErrorCode.PAYMENT_PENDING: "We're checking your payment status.",
    ErrorCode.SERVICE_UNAVAILABLE: "This is unavailable right now. Everything else still works.",
    ErrorCode.INTERNAL_ERROR: "Something went wrong on our side. Please try again.",
}


class ApiError(HTTPException):
    """Raised by a route to answer with one of the documented errors; FastAPI's HTTPException carrying the
    code's status and any headers (for example Retry-After)."""

    def __init__(self, code: ErrorCode, message: str | None = None, details=None, *, headers=None):
        super().__init__(status_code=STATUS[code], detail=message or MESSAGES[code], headers=headers)
        self.code = code
        self.message = self.detail
        self.details = details if details is not None else {}


def field_error(field: str, problem: str, fix: str) -> ApiError:
    """A VALIDATION_ERROR about one field, for a rule only the server can check (a taken email, an unknown store)."""
    return ApiError(ErrorCode.VALIDATION_ERROR, details=[{"field": field, "problem": problem, "fix": fix}])


def error_response(code: ErrorCode, message=None, details=None, headers=None) -> JSONResponse:
    """The one error shape (API 5): {"error": {code, message, details, trace_id}}."""
    body = {"error": {
        "code": code.value,
        "message": message or MESSAGES[code],
        "details": details if details is not None else {},
        "trace_id": trace_id_var.get(),
    }}
    return JSONResponse(status_code=STATUS[code], content=body, headers=headers)


# how to fix each kind of validation problem; the submitted value is never echoed back (it may be a password)
_FIXES = {
    "missing": "Provide this value.",
    "extra_forbidden": "Remove this field.",
    "json_invalid": "Send valid JSON.",
}
_LOCATIONS = ("body", "query", "path", "header", "cookie")


def _problem(error) -> dict:
    location = [str(part) for part in error["loc"]]
    if location and location[0] in _LOCATIONS:
        location = location[1:] or location
    return {
        "field": ".".join(location),
        "problem": error["msg"],
        "fix": _FIXES.get(error["type"], "Correct this value and try again."),
    }


async def _api_error(_request: Request, error: ApiError):
    return error_response(error.code, error.message, error.details, error.headers)


async def _client_database_unavailable(_request: Request, _error: ClientDatabaseUnavailable):
    logger.warning("the client database is unreachable")
    return error_response(ErrorCode.SERVICE_UNAVAILABLE)


async def _validation_error(_request: Request, error: RequestValidationError):
    return error_response(ErrorCode.VALIDATION_ERROR, details=[_problem(e) for e in error.errors()])


# framework errors (unknown route, wrong method) answered in the same shape; 405 means the route does not exist
_HTTP_CODES = {
    status.HTTP_401_UNAUTHORIZED: ErrorCode.AUTH_REQUIRED,
    status.HTTP_403_FORBIDDEN: ErrorCode.FORBIDDEN,
    status.HTTP_404_NOT_FOUND: ErrorCode.NOT_FOUND,
    status.HTTP_405_METHOD_NOT_ALLOWED: ErrorCode.NOT_FOUND,
    status.HTTP_429_TOO_MANY_REQUESTS: ErrorCode.RATE_LIMITED,
}


async def _http_error(_request: Request, error: StarletteHTTPException):
    if error.status_code in _HTTP_CODES:
        code = _HTTP_CODES[error.status_code]
    elif error.status_code >= status.HTTP_500_INTERNAL_SERVER_ERROR:
        code = ErrorCode.INTERNAL_ERROR
    else:
        code = ErrorCode.VALIDATION_ERROR
    return error_response(code, headers=error.headers)  # keeps Allow, Retry-After and the like


def register_error_handlers(app: FastAPI):
    # unexpected exceptions are answered by the trace-ID middleware, which still knows the trace ID
    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(ClientDatabaseUnavailable, _client_database_unavailable)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
