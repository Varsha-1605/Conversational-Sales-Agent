"""Signing up: a new customer is created but not signed in, bad input, and the sign-up limits."""
import re

import pytest
from sqlalchemy import select, text

import backend.api.auth as auth_routes
from backend.auth.passwords import verify_password
from backend.db.schema import customers, journeys, sessions
from tests.signed_in_app import (
    ADMINISTRATOR, ASSOCIATE, CUSTOMER, NEW_CUSTOMER, error_of, session_cookie_of, sign_in, sign_up,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("remove_new_customers_afterwards")]


# sign-up

def test_sign_up_creates_a_base_tier_customer_without_signing_them_in(client, origin, committing_database):
    response = sign_up(client, origin)
    assert response.status_code == 201
    customer_id = response.json()["customer_id"]
    assert re.fullmatch(r"CUST-\d{4}", customer_id)
    assert not session_cookie_of(response)  # the page sends them to sign in
    with committing_database.connect() as connection:
        assert connection.execute(select(sessions.c.session_id).where(
            sessions.c.subject_id == customer_id)).first() is None
    with committing_database.connect() as connection:
        row = connection.execute(select(customers).where(customers.c.customer_id == customer_id)).mappings().one()
        has_journey = connection.execute(select(journeys.c.customer_id).where(
            journeys.c.customer_id == customer_id)).first()
    assert (row["full_name"], row["email"], row["loyalty_tier"], row["points_balance"], row["device_preference"]) == (
        "Asha Verma", "asha.verma@example.com", "Base", 0, "web")
    assert (row["personalization_allowed"], row["save_preferences_allowed"], row["preferred_store_id"]) == (
        False, False, None)
    assert has_journey is not None
    assert row["password_hash"].startswith("$argon2id$") and NEW_CUSTOMER["password"] not in row["password_hash"]
    assert verify_password(NEW_CUSTOMER["password"], row["password_hash"])


def test_a_new_customer_signs_in_after_signing_up_with_the_email_in_any_case(client, origin):
    sign_up(client, origin)
    error_of(client.get("/api/me"), "AUTH_REQUIRED", 401)  # signing up did not sign them in
    assert sign_in(client, origin, NEW_CUSTOMER["email"], NEW_CUSTOMER["password"]).status_code == 200
    me = client.get("/api/me").json()
    assert (me["role"], me["first_name"], me["landing"]) == ("customer", "Asha", "personalization")


@pytest.mark.parametrize("email", [
    CUSTOMER["email"], CUSTOMER["email"].upper(), ASSOCIATE["email"], ADMINISTRATOR["email"]])
def test_an_email_already_used_by_a_customer_or_staff_member_is_refused(client, origin, email):
    response = sign_up(client, origin, email=email)
    body = error_of(response, "VALIDATION_ERROR", 422)
    assert body["details"] == [{"field": "email", "problem": "This email already has an account.",
                                "fix": "Sign in, or use a different email."}]
    assert not session_cookie_of(response)


def test_two_sign_ups_with_one_email_make_one_account(client, origin):
    assert sign_up(client, origin).status_code == 201
    error_of(sign_up(client, origin, email="asha.verma@example.com"), "VALIDATION_ERROR", 422)


@pytest.mark.parametrize("changes, field", [
    ({"full_name": ""}, "full_name"), ({"full_name": "x" * 101}, "full_name"),
    ({"email": "not-an-email"}, "email"), ({"email": "a@b"}, "email"), ({"email": ""}, "email"),
    ({"password": "short"}, "password"), ({"password": "x" * 129}, "password"),
])
def test_an_invalid_field_is_named_and_nothing_is_created(client, origin, committing_database, changes, field):
    with committing_database.connect() as connection:
        before = connection.execute(text("SELECT count(*) FROM customers")).scalar_one()
    response = sign_up(client, origin, **changes)
    body = error_of(response, "VALIDATION_ERROR", 422)
    assert field in {problem["field"] for problem in body["details"]}
    assert all(problem["problem"] and problem["fix"] for problem in body["details"])
    assert changes.get("password", "no-password") not in response.text  # what was typed is never echoed back
    with committing_database.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM customers")).scalar_one() == before


def test_a_missing_field_or_an_extra_field_is_refused(client, origin):
    missing = client.post("/api/auth/sign-up", json={"email": NEW_CUSTOMER["email"]}, headers=origin)
    error_of(missing, "VALIDATION_ERROR", 422)
    error_of(sign_up(client, origin, loyalty_tier="Gold"), "VALIDATION_ERROR", 422)


# one address cannot flood sign-ups

def test_one_address_may_make_ten_sign_up_attempts_an_hour(client, origin, clock):
    taken = {**NEW_CUSTOMER, "email": CUSTOMER["email"]}  # each attempt is refused cheaply: the email has an account
    for _ in range(10):
        error_of(client.post("/api/auth/sign-up", json=taken, headers=origin), "VALIDATION_ERROR", 422)
    refused = client.post("/api/auth/sign-up", json=taken, headers=origin)
    error_of(refused, "RATE_LIMITED", 429)
    assert int(refused.headers["Retry-After"]) > 0
    clock.advance(hours=1, seconds=1)
    error_of(client.post("/api/auth/sign-up", json=taken, headers=origin), "VALIDATION_ERROR", 422)


def test_a_sign_up_that_fails_validation_is_not_counted(client, origin):
    for _ in range(12):  # refused before any work is done, so it is not an attempt to limit
        error_of(sign_up(client, origin, password="short"), "VALIDATION_ERROR", 422)
    assert sign_up(client, origin).status_code == 201


# two sign-ups with one email at the same moment

def test_two_sign_ups_with_one_email_at_the_same_moment_get_the_friendly_answer(client, origin, monkeypatch):
    assert sign_up(client, origin).status_code == 201
    real_check = auth_routes.email_is_taken
    checks = []

    def blind_the_first_time(postgres, sqlserver_engine, email):
        checks.append(email)  # the second request checks before the first has saved: it sees nothing yet
        return False if len(checks) == 1 else real_check(postgres, sqlserver_engine, email)

    monkeypatch.setattr(auth_routes, "email_is_taken", blind_the_first_time)
    body = error_of(sign_up(client, origin), "VALIDATION_ERROR", 422)  # the database's own unique rule catches it
    assert body["details"][0]["field"] == "email" and "already has an account" in body["details"][0]["problem"]
    assert len(checks) == 2
