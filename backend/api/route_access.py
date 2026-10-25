from typing import Annotated

from fastapi import Depends, Request

from backend.api.errors import ApiError, ErrorCode
from backend.auth.session_cookie import COOKIE_NAME, CookieClaims, read_session_cookie
from backend.auth.sessions import Session, find_open_session, record_activity
from backend.domain.transaction import open_transaction

# Who is calling a route and what they may do (API 2). The cookie must be genuine, its session must be open (not
# signed out, not idle past its limit), and the session row decides the role and store: never the request.
# Customer routes load data by session.subject_id only, so a customer_id in a request is ignored (CH-14).
#
# The app's parts are on app.state, set in backend/main.py: settings, clock, the two engines, request_limiter.


def _claims(request: Request) -> CookieClaims:
    key = request.app.state.settings.session_cookie_key.get_secret_value()
    claims = read_session_cookie(key, request.cookies.get(COOKIE_NAME))
    if claims is None:
        raise ApiError(ErrorCode.AUTH_REQUIRED)
    return claims


def _require_matching(session: Session | None, claims: CookieClaims) -> Session:
    if session is None or (session.subject_id, session.role) != (claims.subject_id, claims.role):
        raise ApiError(ErrorCode.AUTH_REQUIRED)
    return session


def current_session(request: Request) -> Session:
    """The signed-in session, counted as activity: it slides the idle limit. Raises AUTH_REQUIRED, or RATE_LIMITED
    when one session makes more than rate_limit.REQUESTS_PER_SESSION requests in a window."""
    state = request.app.state
    claims = _claims(request)
    wait = state.request_limiter.seconds_until_allowed(claims.session_id)
    if wait:
        raise ApiError(ErrorCode.RATE_LIMITED, headers={"Retry-After": str(wait)})
    state.request_limiter.record(claims.session_id)
    now = state.clock.now()
    with open_transaction(state.postgres, state.clock) as transaction:
        session = find_open_session(transaction.connection, now, claims.session_id)
        if session is not None:
            record_activity(transaction.connection, now, session.session_id)
    return _require_matching(session, claims)


def session_without_activity(request: Request) -> Session:
    """The signed-in session without counting the request as activity, for the live stream: an open stream must not
    keep an idle session alive (API 2)."""
    state = request.app.state
    claims = _claims(request)
    with state.postgres.connect() as connection:
        session = find_open_session(connection, state.clock.now(), claims.session_id)
    return _require_matching(session, claims)


def require_role(*allowed: str):
    """A dependency for a route that only some roles may use; the route receives the session. A role mismatch
    returns FORBIDDEN, no session AUTH_REQUIRED."""

    def check(session: Annotated[Session, Depends(current_session)]) -> Session:
        if session.role not in allowed:
            raise ApiError(ErrorCode.FORBIDDEN)
        return session

    return check
