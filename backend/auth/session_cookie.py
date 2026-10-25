from dataclasses import dataclass

import jwt
from starlette.responses import Response

from backend.auth.sessions import Session
from backend.domain.clock import Clock

# API 2: a signed, HTTP-only, Secure, SameSite=Lax cookie that names the session. Role and store in it are only a
# cross-check: the session row is what is trusted. No API key or bearer token is accepted from a browser.

COOKIE_NAME = "session"
ALGORITHM = "HS256"
_REQUIRED = ["sid", "sub", "role", "iat"]


@dataclass(frozen=True)
class CookieClaims:
    session_id: str
    subject_id: str
    role: str


def sign_session_cookie(key: str, session: Session, clock: Clock) -> str:
    claims = {"sid": session.session_id, "sub": session.subject_id, "role": session.role,
              "store": session.store_id, "iat": int(clock.now().timestamp())}
    return jwt.encode(claims, key, algorithm=ALGORITHM)


def read_session_cookie(key: str, value: str | None) -> CookieClaims | None:
    """The claims of a genuine cookie, or None for a missing, altered or foreign one. Whether the session is still
    open is not decided here but by the session row."""
    if not value:
        return None
    try:
        claims = jwt.decode(value, key, algorithms=[ALGORITHM], options={
            "require": _REQUIRED, "verify_exp": False, "verify_iat": False})
    except jwt.PyJWTError:
        return None
    return CookieClaims(session_id=str(claims["sid"]), subject_id=str(claims["sub"]), role=str(claims["role"]))


def set_session_cookie(response: Response, value: str):
    # no max-age: the browser drops it when closed, and idle expiry is enforced on the server
    response.set_cookie(COOKIE_NAME, value, httponly=True, secure=True, samesite="lax", path="/")


def clear_session_cookie(response: Response):
    response.delete_cookie(COOKIE_NAME, httponly=True, secure=True, samesite="lax", path="/")
