import json
import logging
from typing import Annotated

import pytest
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.testclient import TestClient
from pydantic import Field

from backend.api.dependencies import CHANNELS, PageRequest, channel, pagination
from backend.api.errors import STATUS, ApiError, ErrorCode
from backend.api.models import ApiModel, Page
from backend.logging_setup import configure_logging
from backend.main import create_app
from config import Settings

ORIGIN = "http://localhost:8000"
SAME_ORIGIN = {"Origin": ORIGIN}

# routes used only by these tests, to exercise the shared conventions every real route will use
router = APIRouter(prefix="/api")


class Item(ApiModel):
    sku: str
    quantity: int = Field(ge=1, le=10)


@router.get("/fail/{code}")
def fail(code: ErrorCode):
    raise ApiError(code)


@router.get("/crash")
def crash():
    raise RuntimeError("database password is hunter2")


@router.get("/limited")
def limited():
    raise ApiError(ErrorCode.RATE_LIMITED, headers={"Retry-After": "60"})


@router.get("/framework-limited")
def framework_limited():
    # a framework-style error (as a library or Starlette would raise) keeps its headers too
    raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, headers={"Retry-After": "30"})


@router.get("/logs")
def logs():
    logging.getLogger("orders").info("looking up an order")
    return {}


@router.post("/items")
def add_item(item: Item):
    return item


@router.post("/journey-change")
def journey_change(source: Annotated[str, Depends(channel)]):
    return {"channel": source}


@router.get("/numbers")
def numbers(page: Annotated[PageRequest, Depends(pagination)]) -> Page[int]:
    everything = list(range(1, 46))
    return Page(items=everything[page.offset:page.offset + page.page_size], page=page.page,
                page_size=page.page_size, total=len(everything))


def make_client(frontend_dir):
    settings = Settings(_env_file=None, postgres_db="sales", postgres_user="u", postgres_password="p",
                        sql_app_user="app_reader", sql_app_password="p", allowed_origin=ORIGIN)
    # no `with`: the app's startup (database engines) is not needed for these tests
    return TestClient(create_app(settings, routers=[router], frontend_dir=frontend_dir))


@pytest.fixture
def client(tmp_path):
    (tmp_path / "index.html").write_text("<h1>home</h1>", encoding="utf-8")
    return make_client(tmp_path)


def assert_error(response, code):
    assert response.status_code == STATUS[code]
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "details", "trace_id"}
    assert body["error"]["code"] == code.value
    assert body["error"]["message"]
    assert body["error"]["trace_id"] == response.headers["X-Trace-Id"]
    return body["error"]


@pytest.mark.parametrize("code", list(ErrorCode))
def test_every_error_code_has_the_documented_shape(client, code):
    assert_error(client.get(f"/api/fail/{code.value}"), code)


def test_every_response_carries_a_new_trace_id(client):
    first = client.get("/api/numbers")
    second = client.get("/api/numbers")
    assert first.status_code == 200
    assert len(first.headers["X-Trace-Id"]) == 32
    assert first.headers["X-Trace-Id"] != second.headers["X-Trace-Id"]


def test_an_error_keeps_its_headers(client):
    assert assert_error(client.get("/api/limited"), ErrorCode.RATE_LIMITED)
    assert client.get("/api/limited").headers["Retry-After"] == "60"
    response = client.get("/api/framework-limited")
    assert_error(response, ErrorCode.RATE_LIMITED)
    assert response.headers["Retry-After"] == "30"


def test_wrong_method_keeps_the_allow_header(client):
    response = client.delete("/api/numbers", headers=SAME_ORIGIN)
    assert_error(response, ErrorCode.NOT_FOUND)
    assert "GET" in response.headers["Allow"]


def test_log_lines_inside_a_request_carry_its_trace_id(client, restore_logging, capsys):
    configure_logging()
    response = client.get("/api/logs")
    entries = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    route_line = next(entry for entry in entries if entry["message"] == "looking up an order")
    assert route_line["trace_id"] == response.headers["X-Trace-Id"]


def test_unknown_api_route_is_not_found(client):
    assert_error(client.get("/api/no-such-route"), ErrorCode.NOT_FOUND)
    assert_error(client.post("/api/no-such-route", headers=SAME_ORIGIN), ErrorCode.NOT_FOUND)


def test_validation_error_names_the_fields_and_never_echoes_input(client):
    response = client.post("/api/items", headers=SAME_ORIGIN, json={"sku": "S1", "password": "hunter2"})
    error = assert_error(response, ErrorCode.VALIDATION_ERROR)
    fields = {problem["field"]: problem for problem in error["details"]}
    assert set(fields) == {"quantity", "password"}
    assert fields["quantity"]["fix"] == "Provide this value."
    assert fields["password"]["fix"] == "Remove this field."
    assert "hunter2" not in response.text


def test_out_of_range_value_is_a_validation_error(client):
    error = assert_error(client.post("/api/items", headers=SAME_ORIGIN, json={"sku": "S1", "quantity": 11}),
                         ErrorCode.VALIDATION_ERROR)
    assert error["details"][0]["field"] == "quantity"


def test_valid_body_is_accepted(client):
    response = client.post("/api/items", headers=SAME_ORIGIN, json={"sku": "S1", "quantity": 2})
    assert response.status_code == 200
    assert response.json() == {"sku": "S1", "quantity": 2}


def test_unexpected_error_gives_a_generic_answer(client):
    response = client.get("/api/crash")
    error = assert_error(response, ErrorCode.INTERNAL_ERROR)
    assert "hunter2" not in response.text
    assert "Traceback" not in response.text
    assert error["details"] == {}


@pytest.mark.parametrize("headers", [{}, {"Origin": "https://evil.example"}, {"Origin": "http://localhost:9999"}])
def test_state_changing_request_from_another_origin_is_forbidden(client, headers):
    response = client.post("/api/items", headers=headers, json={"sku": "S1", "quantity": 1})
    assert_error(response, ErrorCode.FORBIDDEN)


def test_reading_needs_no_origin(client):
    assert client.get("/api/numbers").status_code == 200


@pytest.mark.parametrize("headers", [{}, {"X-Channel": "voice"}, {"X-Channel": "email"}])
def test_journey_change_needs_a_known_channel(client, headers):
    error = assert_error(client.post("/api/journey-change", headers={**SAME_ORIGIN, **headers}),
                         ErrorCode.VALIDATION_ERROR)
    assert error["details"][0]["field"] == "X-Channel"


@pytest.mark.parametrize("source", CHANNELS)
def test_known_channels_are_accepted(client, source):
    response = client.post("/api/journey-change", headers={**SAME_ORIGIN, "X-Channel": source})
    assert response.json() == {"channel": source}


def test_pagination_defaults_and_shape(client):
    body = client.get("/api/numbers").json()
    assert body == {"items": list(range(1, 21)), "page": 1, "page_size": 20, "total": 45}
    last = client.get("/api/numbers", params={"page": 3}).json()
    assert last["items"] == list(range(41, 46))


@pytest.mark.parametrize("params", [{"page": 0}, {"page_size": 0}, {"page_size": 51}, {"page": "x"}])
def test_pagination_limits(client, params):
    assert_error(client.get("/api/numbers", params=params), ErrorCode.VALIDATION_ERROR)


def test_without_a_frontend_folder_unknown_paths_are_not_found(tmp_path):
    # the real repository has no frontend/ until Phase 5; nothing may turn into an internal error
    client = make_client(tmp_path / "no-frontend-yet")
    assert_error(client.get("/"), ErrorCode.NOT_FOUND)
    assert_error(client.get("/api/no-such-route"), ErrorCode.NOT_FOUND)
    assert client.get("/api/numbers").status_code == 200


def test_frontend_files_are_served(client):
    for path in ("/", "/index.html"):
        response = client.get(path)
        assert response.status_code == 200
        assert "home" in response.text
    assert_error(client.get("/missing-page.html"), ErrorCode.NOT_FOUND)
