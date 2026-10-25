"""Signing in: credentials, roles, the rate limits, a kiosk, and what is never stored or logged."""

import pytest
from sqlalchemy import select, text

from backend.db.schema import customers
from tests.signed_in_app import (
    ADMINISTRATOR, ASSOCIATE, CUSTOMER, DEMO_CUSTOMERS, DEMO_STAFF, LANDING, NEW_CUSTOMER, error_of, running_app,
    session_cookie_of, sign_in, sign_in_as, sign_up,
)

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("remove_new_customers_afterwards")]


# every seeded account signs in with its documented demo credentials

@pytest.mark.parametrize("account", DEMO_CUSTOMERS, ids=[row["customer_id"] for row in DEMO_CUSTOMERS])
def test_every_seeded_customer_signs_in_with_the_demo_password(client, origin, account):
    response = sign_in_as(client, origin, account)
    assert response.status_code == 200
    assert response.json() == {"subject_id": account["customer_id"], "role": "customer", "store_id": None,
                               "landing": "home"}
    assert session_cookie_of(response)


@pytest.mark.parametrize("account", DEMO_STAFF, ids=[row["staff_id"] for row in DEMO_STAFF])
def test_every_staff_account_signs_in_with_the_demo_password(client, origin, account):
    response = sign_in_as(client, origin, account, role=account["role"])
    assert response.status_code == 200
    assert response.json() == {
        "subject_id": account["staff_id"], "role": account["role"],
        "store_id": None if account["location_id"] == "null" else account["location_id"],
        "landing": LANDING[account["role"]]}


def test_the_email_may_be_typed_in_any_case_with_spaces_around_it(client, origin):
    response = sign_in(client, origin, f"  {CUSTOMER['email'].upper()} ", CUSTOMER["password"])
    assert response.status_code == 200


# wrong credentials and wrong roles

def test_a_wrong_password_and_an_unknown_email_get_the_same_answer(client, origin):
    wrong_password = sign_in(client, origin, CUSTOMER["email"], "not-the-password")
    unknown_email = sign_in(client, origin, "nobody@example.com", "not-the-password")
    first = error_of(wrong_password, "AUTH_FAILED", 401)
    second = error_of(unknown_email, "AUTH_FAILED", 401)
    assert first["message"] == second["message"] == "Email or password is incorrect."
    assert not session_cookie_of(wrong_password) and not session_cookie_of(unknown_email)


def test_the_wrong_role_is_refused_after_a_right_password_and_gives_no_session(client, origin):
    response = sign_in_as(client, origin, CUSTOMER, role="store_associate")
    body = error_of(response, "ROLE_MISMATCH", 403)
    assert body["message"] == ("This account is not registered as store associate. "
                               "Check the role you selected and try again.")
    assert not session_cookie_of(response)


@pytest.mark.parametrize("account, role", [
    (CUSTOMER, "administrator"), (ASSOCIATE, "customer"), (ASSOCIATE, "administrator"), (ADMINISTRATOR, "customer"),
])
def test_a_customer_cannot_sign_in_as_staff_and_the_reverse(client, origin, account, role):
    error_of(sign_in_as(client, origin, account, role=role), "ROLE_MISMATCH", 403)


def test_a_wrong_password_hides_the_role_check(client, origin):
    response = sign_in(client, origin, CUSTOMER["email"], "wrong", role="administrator")
    error_of(response, "AUTH_FAILED", 401)  # not ROLE_MISMATCH: that would confirm the email exists


# the rate limit

def test_the_sixth_attempt_is_refused_even_with_the_right_password(client, origin):
    for _ in range(5):
        error_of(sign_in(client, origin, CUSTOMER["email"], "wrong"), "AUTH_FAILED", 401)
    refused = sign_in_as(client, origin, CUSTOMER)
    error_of(refused, "RATE_LIMITED", 429)
    assert int(refused.headers["Retry-After"]) > 0


def test_a_wrong_role_counts_toward_the_limit(client, origin):
    for _ in range(5):
        error_of(sign_in_as(client, origin, CUSTOMER, role="administrator"), "ROLE_MISMATCH", 403)
    error_of(sign_in_as(client, origin, CUSTOMER), "RATE_LIMITED", 429)


def test_an_unknown_email_is_limited_the_same_way_so_nothing_is_revealed(client, origin):
    for _ in range(5):
        error_of(sign_in(client, origin, "nobody@example.com", "x"), "AUTH_FAILED", 401)
    error_of(sign_in(client, origin, "nobody@example.com", "x"), "RATE_LIMITED", 429)


def test_the_limit_is_per_email_and_lifts_after_fifteen_minutes(client, origin, clock):
    for _ in range(5):
        sign_in(client, origin, CUSTOMER["email"], "wrong")
    assert sign_in_as(client, origin, DEMO_CUSTOMERS[0]).status_code == 200  # another email is not affected
    error_of(sign_in_as(client, origin, CUSTOMER), "RATE_LIMITED", 429)
    clock.advance(minutes=15, seconds=1)
    assert sign_in_as(client, origin, CUSTOMER).status_code == 200


def test_a_successful_sign_in_starts_the_count_again(client, origin):
    for _ in range(4):
        sign_in(client, origin, CUSTOMER["email"], "wrong")
    assert sign_in_as(client, origin, CUSTOMER).status_code == 200
    for _ in range(4):
        error_of(sign_in(client, origin, CUSTOMER["email"], "wrong"), "AUTH_FAILED", 401)


# a kiosk

def test_a_kiosk_sign_in_keeps_the_store_the_kiosk_is_set_up_for(client, origin):
    response = sign_in_as(client, origin, CUSTOMER, **{"X-Channel": "kiosk", "store_id": "LOC-S01"})
    assert response.status_code == 200 and response.json()["store_id"] == "LOC-S01"


@pytest.mark.parametrize("channel, store_id", [
    ("kiosk", "LOC-W01"),      # the warehouse is not a store
    ("kiosk", "LOC-NOWHERE"),  # not a location at all
    ("web", "LOC-S01"),        # only a kiosk sets a store
])
def test_a_store_id_is_accepted_only_from_a_kiosk_and_only_for_a_store(client, origin, channel, store_id):
    response = sign_in_as(client, origin, CUSTOMER, **{"X-Channel": channel, "store_id": store_id})
    body = error_of(response, "VALIDATION_ERROR", 422)
    assert body["details"][0]["field"] == "store_id"
    assert not session_cookie_of(response)


def test_an_unknown_channel_is_refused(client, origin):
    error_of(sign_in_as(client, origin, CUSTOMER, **{"X-Channel": "fax"}), "VALIDATION_ERROR", 422)


# forgery and secrets

def test_sign_in_sign_up_and_sign_out_refuse_a_foreign_origin(client):
    foreign = {"Origin": "https://evil.example"}
    for path in ("/api/auth/sign-in", "/api/auth/sign-up", "/api/auth/sign-out"):
        error_of(client.post(path, json={}, headers=foreign), "FORBIDDEN", 403)


def test_no_plain_password_is_stored_anywhere(committing_database, sql_server):
    with committing_database.connect() as connection:
        columns = set(connection.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public'")).scalars())
        hashes = connection.execute(select(customers.c.password_hash)).scalars().all()
    assert "password" not in columns
    assert hashes and all(value.startswith("$argon2id$") for value in hashes)
    with sql_server.connect() as connection:
        staff_columns = set(connection.execute(text(
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = 'staff_data'")).scalars())
        staff_hashes = connection.execute(text("SELECT password_hash FROM dbo.staff_data")).scalars().all()
    assert staff_columns == {"staff_id", "name", "email", "role", "location_id", "password_hash"}
    assert len(staff_hashes) == len(DEMO_STAFF) and all(value.startswith("$argon2id$") for value in staff_hashes)


def test_passwords_and_whole_emails_are_never_logged(
        capsys, restore_logging, committing_database, test_settings, clock, tmp_path, origin):
    with running_app(test_settings, clock, tmp_path) as client:
        sign_in(client, origin, CUSTOMER["email"], "typed-wrong-password-1")
        sign_in_as(client, origin, CUSTOMER, role="administrator")
        sign_in_as(client, origin, DEMO_CUSTOMERS[0])
        sign_up(client, origin)
    output = capsys.readouterr().out
    assert "signed in" in output and "sign-in refused" in output  # the events are logged
    for secret in ("typed-wrong-password-1", CUSTOMER["password"], DEMO_CUSTOMERS[0]["password"],
                   NEW_CUSTOMER["password"],
                   CUSTOMER["email"], NEW_CUSTOMER["email"].lower()):
        assert secret not in output
    assert "r***@outlook.com" in output


# one address cannot spray endless emails (each attempt costs a password hash)

def test_one_address_cannot_try_endless_different_emails(client, origin, clock):
    for number in range(30):
        error_of(sign_in(client, origin, f"nobody{number}@example.com", "x"), "AUTH_FAILED", 401)
    refused = sign_in(client, origin, "another@example.com", "x")
    error_of(refused, "RATE_LIMITED", 429)
    assert int(refused.headers["Retry-After"]) > 0
    error_of(sign_in_as(client, origin, CUSTOMER), "RATE_LIMITED", 429)  # even a right password waits
    clock.advance(minutes=15, seconds=1)
    assert sign_in_as(client, origin, CUSTOMER).status_code == 200


def test_successful_sign_ins_do_not_count_toward_the_address_limit(client, origin):
    for _ in range(31):  # people sharing an address (a store's kiosks) are not blocked by their own sign-ins
        assert sign_in_as(client, origin, CUSTOMER).status_code == 200
