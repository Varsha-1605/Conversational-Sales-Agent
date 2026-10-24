import json
import logging

import pytest

from backend.logging_setup import configure_logging, mask_email, mask_name, trace_id_var


@pytest.fixture
def restore_root_logger():
    # configure_logging replaces the root handlers; put pytest's back afterwards
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


def last_json_line(capsys):
    return capsys.readouterr().out.strip().splitlines()[-1]


def test_mask_name():
    assert mask_name("Varsha Dewangan") == "V*** D***"
    assert mask_name("Asha") == "A***"
    assert mask_name("") == "***"
    assert mask_name(None) == "***"


def test_mask_email():
    assert mask_email("varsha@example.com") == "v***@example.com"
    assert mask_email("not-an-email") == "***"
    assert mask_email(None) == "***"


def test_log_line_is_json_with_trace_id_and_no_personal_data(restore_root_logger, capsys):
    configure_logging()  # inside the test, so the handler writes to capsys's stdout
    token = trace_id_var.set("trace-123")
    try:
        logging.getLogger("orders").info(
            "order placed for %s (%s)", mask_name("Varsha Dewangan"), mask_email("varsha@example.com")
        )
    finally:
        trace_id_var.reset(token)

    line = last_json_line(capsys)
    entry = json.loads(line)
    assert entry["trace_id"] == "trace-123"
    assert entry["level"] == "INFO"
    assert entry["logger"] == "orders"
    assert entry["time"].endswith("+05:30")
    assert "Varsha" not in line
    assert "varsha@" not in line


def test_trace_id_is_null_outside_a_request(restore_root_logger, capsys):
    configure_logging()
    logging.getLogger("startup").info("ready")
    entry = json.loads(last_json_line(capsys))
    assert entry["trace_id"] is None
