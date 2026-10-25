"""What tests that sign in through the real app share: the demo accounts, an app running on the test database, and
small helpers for signing in and reading error answers."""
import csv
from contextlib import contextmanager

from fastapi.testclient import TestClient

from backend.api.router import api_router
from backend.auth.session_cookie import COOKIE_NAME
from backend.main import create_app
from config import PROJECT_ROOT


def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as file:
        return list(csv.DictReader(file))


# the documented demo accounts, with their demo passwords, as loaded by the seed and the loader
DEMO_CUSTOMERS = read_csv(PROJECT_ROOT / "data" / "developers" / "customers.csv")
DEMO_STAFF = read_csv(PROJECT_ROOT / "data" / "raw" / "staff_data.csv")
CUSTOMER = DEMO_CUSTOMERS[1]  # Rahul Nair, Silver
ASSOCIATE = next(row for row in DEMO_STAFF if row["role"] == "store_associate")
ADMINISTRATOR = next(row for row in DEMO_STAFF if row["role"] == "administrator")


# the page each role lands on after sign-in (a customer with no preferred store lands on personalization instead)
LANDING = {"customer": "home", "store_associate": "associate_home", "administrator": "integration_status"}
NEW_CUSTOMER = {"full_name": "Asha Verma", "email": "Asha.Verma@Example.com", "password": "A-good-pass-1"}


@contextmanager
def running_app(test_settings, clock, frontend_dir, extra_routers=()):
    app = create_app(test_settings, routers=[api_router, *extra_routers], frontend_dir=frontend_dir, clock=clock)
    # https, because the session cookie is Secure and a client sends it only over https
    with TestClient(app, base_url="https://testserver") as client:
        yield client


def sign_in(client, origin, email, password, role="customer", **extra_headers):
    body = {"email": email, "password": password, "role": role}
    store_id = extra_headers.pop("store_id", None)
    if store_id:
        body["store_id"] = store_id
    return client.post("/api/auth/sign-in", json=body, headers={**origin, **extra_headers})


def sign_in_as(client, origin, account, role="customer", **extra_headers):
    return sign_in(client, origin, account["email"], account["password"], role, **extra_headers)


def sign_up(client, origin, **changes):
    return client.post("/api/auth/sign-up", json={**NEW_CUSTOMER, **changes}, headers=origin)


def error_of(response, code, status):
    assert response.status_code == status
    body = response.json()["error"]
    assert body["code"] == code
    return body


def session_cookie_of(response):
    return next((cookie for cookie in response.headers.get_list("set-cookie")
                 if cookie.startswith(f"{COOKIE_NAME}=")), None)
