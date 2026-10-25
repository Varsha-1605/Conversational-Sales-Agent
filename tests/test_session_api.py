"""A signed-in session: the cookie, who-am-I, role and ownership checks, idle expiry and sign-out."""

import jwt
import pytest
from sqlalchemy import select, text, update

from backend.auth.session_cookie import COOKIE_NAME
from backend.db.schema import journeys, sessions
from tests.signed_in_app import (
    ADMINISTRATOR, ASSOCIATE, CUSTOMER, DEMO_CUSTOMERS, LANDING, error_of, running_app,
    session_cookie_of, sign_in_as,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("remove_new_customers_afterwards")]

from typing import Annotated

from fastapi import APIRouter, Depends

from backend.api.route_access import require_role, session_without_activity
from backend.auth.sessions import Session


# routes that exist only for these tests, to exercise what every real route will use
routes_for_tests = APIRouter(prefix="/api/_test")


@routes_for_tests.get("/customer-only")
def customer_only(session: Annotated[Session, Depends(require_role("customer"))]):
    return {"customer_id": session.subject_id}


@routes_for_tests.get("/staff-only")
def staff_only(session: Annotated[Session, Depends(require_role("store_associate", "administrator"))]):
    return {"staff_id": session.subject_id, "store_id": session.store_id}


@routes_for_tests.get("/mine")
def mine(session: Annotated[Session, Depends(require_role("customer"))], customer_id: str | None = None):
    return {"customer_id": session.subject_id}  # a customer_id in the request is not used


@routes_for_tests.get("/stream")
def stream(session: Annotated[Session, Depends(session_without_activity)]):
    return {"subject_id": session.subject_id}


@pytest.fixture
def client(restore_logging, test_settings, clock, tmp_path):
    """The app on the test database, with the test-only routes."""
    with running_app(test_settings, clock, tmp_path, extra_routers=[routes_for_tests]) as client:
        yield client


def test_one_session_may_make_120_requests_a_minute(client, origin, clock):
    sign_in_as(client, origin, CUSTOMER)
    for _ in range(120):
        assert client.get("/api/_test/customer-only").status_code == 200
    refused = client.get("/api/_test/customer-only")
    error_of(refused, "RATE_LIMITED", 429)
    assert int(refused.headers["Retry-After"]) > 0
    clock.advance(minutes=1, seconds=1)
    assert client.get("/api/_test/customer-only").status_code == 200


# the cookie and the session behind it

def test_the_cookie_is_signed_http_only_secure_and_same_site(client, origin, test_settings):
    cookie = session_cookie_of(sign_in_as(client, origin, CUSTOMER))
    parts = {part.strip().lower() for part in cookie.split(";")}
    assert {"httponly", "secure", "samesite=lax", "path=/"} <= parts
    assert not any(part.startswith(("max-age", "expires")) for part in parts)
    value = cookie.split(";")[0].split("=", 1)[1]
    claims = jwt.decode(value, test_settings.session_cookie_key.get_secret_value(), algorithms=["HS256"],
                        options={"verify_iat": False})
    assert (claims["sub"], claims["role"], claims["store"]) == (CUSTOMER["customer_id"], "customer", None)
    assert CUSTOMER["password"] not in value


def test_the_cookie_is_signed_with_its_own_key_not_the_token_key(client, origin, test_settings):
    value = session_cookie_of(sign_in_as(client, origin, CUSTOMER)).split(";")[0].split("=", 1)[1]
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(value, test_settings.token_signing_key.get_secret_value(), algorithms=["HS256"],
                   options={"verify_iat": False})


def test_the_session_row_holds_role_and_store_from_the_database_not_the_request(
        client, origin, committing_database):
    response = sign_in_as(client, origin, ASSOCIATE, role="store_associate", store_id=None)
    assert response.json()["store_id"] == ASSOCIATE["location_id"]
    with committing_database.connect() as connection:
        row = connection.execute(
            select(sessions).where(sessions.c.subject_id == ASSOCIATE["staff_id"])).mappings().one()
    assert (row["role"], row["store_id"], row["channel"], row["ended_at"]) == (
        "store_associate", ASSOCIATE["location_id"], "web", None)


def test_the_app_runs_on_its_own_postgres_login(client, test_settings):
    with client.app.state.postgres.connect() as connection:
        assert connection.execute(text("SELECT current_user")).scalar_one() == test_settings.postgres_app_user


# who am I

@pytest.mark.parametrize("account, role, first_name, subject", [
    (CUSTOMER, "customer", "Rahul", "customer_id"),
    (ASSOCIATE, "store_associate", ASSOCIATE["name"].split()[0], "staff_id"),
    (ADMINISTRATOR, "administrator", ADMINISTRATOR["name"].split()[0], "staff_id"),
])
def test_me_says_who_is_signed_in(client, origin, account, role, first_name, subject):
    sign_in_as(client, origin, account, role=role)
    body = client.get("/api/me").json()
    assert body["subject_id"] == account[subject]
    assert (body["role"], body["first_name"], body["landing"]) == (role, first_name, LANDING[role])
    assert "password" not in str(body)


def test_without_a_session_every_route_asks_for_sign_in(client):
    for path in ("/api/me", "/api/_test/customer-only", "/api/_test/staff-only", "/api/_test/stream"):
        error_of(client.get(path), "AUTH_REQUIRED", 401)


def test_a_cookie_signed_with_another_key_is_refused(client, origin, clock):
    forged = jwt.encode({"sid": "x", "sub": CUSTOMER["customer_id"], "role": "customer", "iat": 1}, "k" * 40,
                        algorithm="HS256")
    error_of(client.get("/api/me", headers={"Cookie": f"{COOKIE_NAME}={forged}"}), "AUTH_REQUIRED", 401)


def test_a_genuine_cookie_whose_session_does_not_exist_is_refused(client, test_settings):
    key = test_settings.session_cookie_key.get_secret_value()
    unknown = jwt.encode({"sid": "no-such-session", "sub": CUSTOMER["customer_id"], "role": "customer", "iat": 1},
                         key, algorithm="HS256")
    error_of(client.get("/api/me", headers={"Cookie": f"{COOKIE_NAME}={unknown}"}), "AUTH_REQUIRED", 401)


def test_a_cookie_cannot_claim_a_different_role_than_its_session(client, origin, test_settings):
    sign_in_as(client, origin, CUSTOMER)
    cookie = client.cookies.get(COOKIE_NAME)
    key = test_settings.session_cookie_key.get_secret_value()
    claims = jwt.decode(cookie, key, algorithms=["HS256"], options={"verify_iat": False})
    claims["role"] = "administrator"
    forged = jwt.encode(claims, key, algorithm="HS256")  # even signed correctly, the session row disagrees
    client.cookies.clear()
    error_of(client.get("/api/me", headers={"Cookie": f"{COOKIE_NAME}={forged}"}), "AUTH_REQUIRED", 401)


# roles and ownership

def test_each_role_reaches_only_its_own_routes(client, origin):
    sign_in_as(client, origin, CUSTOMER)
    assert client.get("/api/_test/customer-only").status_code == 200
    error_of(client.get("/api/_test/staff-only"), "FORBIDDEN", 403)
    client.cookies.clear()
    sign_in_as(client, origin, ASSOCIATE, role="store_associate")
    assert client.get("/api/_test/staff-only").status_code == 200
    error_of(client.get("/api/_test/customer-only"), "FORBIDDEN", 403)


def test_an_associate_session_carries_the_associates_store(client, origin):
    sign_in_as(client, origin, ASSOCIATE, role="store_associate")
    assert client.get("/api/_test/staff-only").json() == {
        "staff_id": ASSOCIATE["staff_id"], "store_id": ASSOCIATE["location_id"]}


def test_a_customer_id_in_the_request_is_ignored_only_the_session_counts(client, origin):
    sign_in_as(client, origin, CUSTOMER)
    answer = client.get("/api/_test/mine", params={"customer_id": DEMO_CUSTOMERS[0]["customer_id"]})
    assert answer.json() == {"customer_id": CUSTOMER["customer_id"]}


# idle expiry

def test_a_session_idle_for_30_minutes_expires_but_one_used_before_then_lives_on(client, origin, clock):
    sign_in_as(client, origin, CUSTOMER)
    clock.advance(minutes=29, seconds=59)
    assert client.get("/api/me").status_code == 200
    clock.advance(minutes=29, seconds=59)  # long after sign-in, but under 30 minutes since the last request
    assert client.get("/api/me").status_code == 200
    clock.advance(minutes=30)
    error_of(client.get("/api/me"), "AUTH_REQUIRED", 401)


def test_a_kiosk_session_expires_after_2_minutes(client, origin, clock):
    sign_in_as(client, origin, CUSTOMER, **{"X-Channel": "kiosk"})
    clock.advance(minutes=1, seconds=59)
    assert client.get("/api/me").status_code == 200
    clock.advance(minutes=2)
    error_of(client.get("/api/me"), "AUTH_REQUIRED", 401)


def test_the_live_stream_does_not_count_as_activity(client, origin, clock):
    sign_in_as(client, origin, CUSTOMER)
    clock.advance(minutes=20)
    assert client.get("/api/_test/stream").status_code == 200  # an open stream is not use
    clock.advance(minutes=11)
    error_of(client.get("/api/me"), "AUTH_REQUIRED", 401)


def test_the_live_stream_still_refuses_an_expired_session(client, origin, clock):
    sign_in_as(client, origin, CUSTOMER)
    clock.advance(minutes=31)
    error_of(client.get("/api/_test/stream"), "AUTH_REQUIRED", 401)


# sign-out

def test_signing_out_ends_the_session_and_clears_the_cookie(client, origin):
    sign_in_as(client, origin, CUSTOMER)
    old_cookie = client.cookies.get(COOKIE_NAME)
    response = client.post("/api/auth/sign-out", headers=origin)
    assert response.status_code == 204 and response.content == b""
    assert 'session=""' in session_cookie_of(response).lower() or "session=;" in session_cookie_of(response).lower()
    client.cookies.clear()
    # the old cookie is still genuine, but its session has ended
    error_of(client.get("/api/me", headers={"Cookie": f"{COOKIE_NAME}={old_cookie}"}), "AUTH_REQUIRED", 401)


def test_sign_out_needs_a_session(client, origin):
    error_of(client.post("/api/auth/sign-out", headers=origin), "AUTH_REQUIRED", 401)


def test_a_session_that_has_already_expired_cannot_be_signed_out_and_the_page_is_told_to_sign_in(
        client, origin, clock):
    sign_in_as(client, origin, CUSTOMER)
    clock.advance(minutes=31)
    error_of(client.post("/api/auth/sign-out", headers=origin), "AUTH_REQUIRED", 401)  # already as good as signed out


@pytest.fixture
def active_channel(committing_database):
    """Read and set a customer's active channel directly; every customer's is put back to empty afterwards."""
    def set_to(channel):
        with committing_database.begin() as connection:
            connection.execute(update(journeys).where(
                journeys.c.customer_id == CUSTOMER["customer_id"]).values(active_channel=channel))

    def read():
        with committing_database.connect() as connection:
            return connection.execute(select(journeys.c.active_channel).where(
                journeys.c.customer_id == CUSTOMER["customer_id"])).scalar_one()

    yield set_to, read
    set_to(None)


def test_signing_out_of_the_channel_that_holds_the_journey_clears_it(client, origin, active_channel):
    set_to, read = active_channel
    set_to("web")
    sign_in_as(client, origin, CUSTOMER)
    client.post("/api/auth/sign-out", headers=origin)
    assert read() is None


def test_signing_out_of_another_channel_leaves_the_journey_where_it_is(client, origin, active_channel):
    set_to, read = active_channel
    set_to("app")
    sign_in_as(client, origin, CUSTOMER)  # a web sign-in
    client.post("/api/auth/sign-out", headers=origin)
    assert read() == "app"


def test_the_sign_out_request_names_the_channel_being_left(client, origin, active_channel):
    set_to, read = active_channel
    set_to("app")
    sign_in_as(client, origin, CUSTOMER)
    client.post("/api/auth/sign-out", headers={**origin, "X-Channel": "app"})
    assert read() is None


def test_signing_out_a_kiosk_clears_the_kiosk_channel(client, origin, active_channel):
    set_to, read = active_channel
    set_to("kiosk")
    sign_in_as(client, origin, CUSTOMER, **{"X-Channel": "kiosk"})
    client.post("/api/auth/sign-out", headers=origin)
    assert read() is None


def test_staff_signing_out_touches_no_journey(client, origin, active_channel):
    set_to, read = active_channel
    set_to("web")
    sign_in_as(client, origin, ASSOCIATE, role="store_associate")
    assert client.post("/api/auth/sign-out", headers=origin).status_code == 204
    assert read() == "web"
