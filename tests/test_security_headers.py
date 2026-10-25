import re

import pytest
from fastapi.testclient import TestClient

from backend.api.middleware import CONTENT_SECURITY_POLICY, SECURITY_HEADERS
from backend.main import create_app
from config import PROJECT_ROOT

# The browser protections on every answer (API 2). No database is needed: these answers are made before any is used.

PAGES = ["/pages/sign-in/", "/pages/sign-up/", "/pages/personalization/"]


@pytest.fixture(scope="module")
def client(settings):
    return TestClient(create_app(settings), base_url="https://testserver")


def assert_protected(response):
    for name, value in SECURITY_HEADERS.items():
        assert response.headers[name] == value, name
    assert response.headers["Content-Security-Policy"] == CONTENT_SECURITY_POLICY


@pytest.mark.parametrize("path", PAGES)
def test_every_page_is_protected_against_framing_and_loading_anything_foreign(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert_protected(response)


def test_the_policy_allows_only_our_own_files_and_no_framing():
    assert "default-src 'self'" in CONTENT_SECURITY_POLICY
    assert "frame-ancestors 'none'" in CONTENT_SECURITY_POLICY
    assert "unsafe" not in CONTENT_SECURITY_POLICY and "*" not in CONTENT_SECURITY_POLICY
    assert SECURITY_HEADERS["X-Frame-Options"] == "DENY"


def test_an_api_answer_is_protected_and_never_kept_by_a_browser_or_proxy(client):
    response = client.get("/api/me")  # no session: AUTH_REQUIRED
    assert response.status_code == 401
    assert_protected(response)
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Trace-Id"]


def test_a_refused_request_and_an_unknown_route_are_protected_too(client, settings):
    refused = client.post("/api/auth/sign-in", json={}, headers={"Origin": "https://evil.example"})
    assert refused.status_code == 403
    assert_protected(refused)
    assert refused.headers["Cache-Control"] == "no-store"
    unknown = client.get("/api/nothing-here")
    assert unknown.status_code == 404
    assert_protected(unknown)


def test_pages_may_be_cached_as_files_are(client):
    assert "no-store" not in client.get("/styles/base.css").headers.get("Cache-Control", "")


def test_the_api_documentation_still_works_without_the_policy_that_would_break_it(client):
    response = client.get("/api/docs")
    assert response.status_code == 200
    assert "Content-Security-Policy" not in response.headers  # its viewer loads from a CDN and runs inline code
    assert response.headers["X-Frame-Options"] == "DENY" and response.headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.parametrize("path", PAGES)
def test_no_page_needs_what_the_policy_forbids(path):
    # inline script, inline style and event-handler attributes would be blocked, so a page must not use them
    html = (PROJECT_ROOT / "frontend" / path.strip("/") / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"<script(?![^>]*\bsrc=)", html)
    assert not re.search(r"<style\b", html)
    assert not re.search(r"\sstyle=", html)
    assert not re.search(r"\son[a-z]+=", html)
    assert "javascript:" not in html
