import pytest
from sqlalchemy import text

from tests.signed_in_app import ASSOCIATE, CUSTOMER, error_of, running_app, session_cookie_of, sign_in, sign_in_as

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("remove_new_customers_afterwards")]

# The client's SQL Server holds staff, products and stock. A customer's account is in our own PostgreSQL, so a
# customer can still sign in and out while the client's database is down; what needs it answers SERVICE_UNAVAILABLE,
# never a server error.
CHOICES = {"name": "Rahul Nair", "gender": "man", "preferred_store_id": "LOC-S02",
           "personalization_allowed": True, "save_preferences_allowed": False}


@pytest.fixture
def client(restore_logging, test_settings, clock, tmp_path):
    unreachable = test_settings.model_copy(update={"sql_server_port": 1, "db_query_timeout_seconds": 1})
    with running_app(unreachable, clock, tmp_path) as client:
        yield client


def assert_unavailable(response):
    body = error_of(response, "SERVICE_UNAVAILABLE", 503)
    assert body["message"] == "This is unavailable right now. Everything else still works."


def test_a_customer_signs_in_asks_who_they_are_and_signs_out(client, origin):
    response = sign_in_as(client, origin, CUSTOMER)
    assert response.status_code == 200 and session_cookie_of(response)
    me = client.get("/api/me")
    assert me.status_code == 200 and me.json()["first_name"] == "Rahul"
    assert client.post("/api/auth/sign-out", headers=origin).status_code == 204


def test_a_wrong_customer_password_is_still_a_wrong_password(client, origin):
    error_of(sign_in(client, origin, CUSTOMER["email"], "not-the-password"), "AUTH_FAILED", 401)


def test_a_staff_sign_in_says_the_service_is_unavailable_and_gives_no_session(client, origin):
    response = sign_in_as(client, origin, ASSOCIATE, role="store_associate")
    assert_unavailable(response)
    assert not session_cookie_of(response)


def test_an_unknown_email_cannot_be_ruled_out_without_the_staff_list(client, origin):
    assert_unavailable(sign_in(client, origin, "nobody@example.com", "x"))


def test_sign_up_waits_because_a_staff_email_cannot_be_ruled_out(client, origin, committing_database):
    with committing_database.connect() as connection:
        before = connection.execute(text("SELECT count(*) FROM customers")).scalar_one()
    body = {"full_name": "Asha Verma", "email": "asha.verma@example.com", "password": "A-good-pass-1"}
    assert_unavailable(client.post("/api/auth/sign-up", json=body, headers=origin))
    with committing_database.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM customers")).scalar_one() == before


def test_personalization_needs_the_store_list_so_it_says_unavailable(client, origin):
    sign_in_as(client, origin, CUSTOMER)
    assert_unavailable(client.get("/api/personalization"))
    assert_unavailable(client.put("/api/personalization", json=CHOICES, headers=origin))
