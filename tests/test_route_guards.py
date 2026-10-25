import re

from fastapi import APIRouter
from fastapi.testclient import TestClient

from backend.api.router import api_router
from backend.main import create_app

# A route is public unless it asks for a session, so a route someone forgets to guard would be open to everyone.
# This test asks every documented route for its answer without a session: it must be "sign in required", except
# for the routes listed here as public on purpose. Adding a public route means adding it to this list, on purpose.
PUBLIC_ROUTES = {("post", "/api/auth/sign-in"), ("post", "/api/auth/sign-up")}
WITH_A_BODY = {"post", "put", "patch", "delete"}


def routes_that_answer_without_a_session(app, origin):
    client = TestClient(app, base_url="https://testserver")
    open_routes = []
    for path, operations in app.openapi()["paths"].items():
        for method in operations:
            if (method, path) in PUBLIC_ROUTES:
                continue
            url = re.sub(r"\{[^}]+\}", "x", path)
            kwargs = {"json": {}} if method in WITH_A_BODY else {}
            response = client.request(method.upper(), url, headers=origin, **kwargs)
            answer = response.json().get("error", {}) if response.headers.get("content-type", "").startswith(
                "application/json") else {}
            if response.status_code != 401 or answer.get("code") != "AUTH_REQUIRED":
                open_routes.append(f"{method.upper()} {path} answered {response.status_code}")
    return open_routes


def test_every_route_but_the_public_ones_asks_for_a_session(settings):
    app = create_app(settings)
    assert routes_that_answer_without_a_session(app, {"Origin": settings.allowed_origin}) == []


def test_the_public_list_names_only_routes_that_exist(settings):
    documented = {(method, path) for path, operations in create_app(settings).openapi()["paths"].items()
                  for method in operations}
    assert PUBLIC_ROUTES <= documented


def test_the_check_would_notice_a_route_left_open(settings):
    careless = APIRouter(prefix="/api")

    @careless.get("/oops/{thing}")
    def oops(thing: str):
        return {"thing": thing}  # no session asked for

    @careless.post("/oops-too")
    def oops_too():
        return {}

    app = create_app(settings, routers=[api_router, careless])
    open_routes = routes_that_answer_without_a_session(app, {"Origin": settings.allowed_origin})
    assert sorted(open_routes) == ["GET /api/oops/{thing} answered 200", "POST /api/oops-too answered 200"]
