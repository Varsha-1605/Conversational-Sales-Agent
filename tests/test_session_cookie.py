from datetime import datetime

import jwt
import pytest
from starlette.responses import Response

from backend.auth.session_cookie import (
    COOKIE_NAME, clear_session_cookie, read_session_cookie, set_session_cookie, sign_session_cookie,
)
from backend.auth.sessions import Session
from backend.domain.clock import IST, FixedClock

KEY = "cookie-key-" + "k" * 32
OTHER_KEY = "other-key-" + "o" * 32
CLOCK = FixedClock(datetime(2026, 10, 25, 12, 0, tzinfo=IST))
SESSION = Session(session_id="abc123", subject_id="CUST-0002", role="customer", store_id=None, channel="web")


def test_a_genuine_cookie_gives_back_who_it_names():
    claims = read_session_cookie(KEY, sign_session_cookie(KEY, SESSION, CLOCK))
    assert (claims.session_id, claims.subject_id, claims.role) == ("abc123", "CUST-0002", "customer")


def test_the_cookie_carries_the_documented_claims_and_no_secret():
    value = sign_session_cookie(KEY, SESSION, CLOCK)
    claims = jwt.decode(value, KEY, algorithms=["HS256"], options={"verify_iat": False})
    assert claims == {"sid": "abc123", "sub": "CUST-0002", "role": "customer", "store": None,
                      "iat": int(CLOCK.now().timestamp())}


@pytest.mark.parametrize("value", [None, "", "not-a-token", "a.b.c"])
def test_a_missing_or_garbled_cookie_names_nobody(value):
    assert read_session_cookie(KEY, value) is None


def test_a_cookie_signed_with_another_key_is_refused():
    assert read_session_cookie(KEY, sign_session_cookie(OTHER_KEY, SESSION, CLOCK)) is None


def test_an_altered_cookie_is_refused():
    head, body, signature = sign_session_cookie(KEY, SESSION, CLOCK).split(".")
    forged_body = jwt.utils.base64url_encode(
        b'{"sid":"abc123","sub":"STF-001","role":"administrator","store":null,"iat":1}').decode()
    assert read_session_cookie(KEY, f"{head}.{forged_body}.{signature}") is None


def test_a_cookie_without_one_of_its_claims_is_refused():
    incomplete = jwt.encode({"sid": "abc123", "sub": "CUST-0002", "iat": 1}, KEY, algorithm="HS256")
    assert read_session_cookie(KEY, incomplete) is None


def test_a_cookie_that_asks_for_no_signature_is_refused():
    unsigned = jwt.encode({"sid": "abc123", "sub": "CUST-0002", "role": "customer", "iat": 1}, None, algorithm="none")
    assert read_session_cookie(KEY, unsigned) is None


def test_the_cookie_is_http_only_secure_and_same_site_lax_with_no_lifetime():
    response = Response()
    set_session_cookie(response, "value")
    header = response.headers["set-cookie"].lower()
    assert header.startswith(f"{COOKIE_NAME}=value")
    assert {"httponly", "secure", "samesite=lax", "path=/"} <= set(part.strip() for part in header.split(";"))
    assert "max-age" not in header and "expires" not in header


def test_clearing_the_cookie_keeps_the_same_flags():
    response = Response()
    clear_session_cookie(response)
    header = response.headers["set-cookie"].lower()
    assert 'session=""' in header or "session=;" in header
    assert "httponly" in header and "secure" in header and "samesite=lax" in header
