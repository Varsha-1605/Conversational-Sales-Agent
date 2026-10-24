import json
import logging

from backend.logging_setup import configure_logging, mask_email, mask_name, trace_id_var


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


def test_log_line_is_json_with_trace_id_and_no_personal_data(restore_logging, capsys):
    configure_logging()
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


def test_trace_id_is_null_outside_a_request(restore_logging, capsys):
    configure_logging()
    logging.getLogger("startup").info("ready")
    entry = json.loads(last_json_line(capsys))
    assert entry["trace_id"] is None


def test_server_lines_are_json_too(restore_logging, capsys):
    # uvicorn gives its loggers plain-text handlers and turns propagation off at start-up
    access = logging.getLogger("uvicorn.access")
    access.handlers[:] = [logging.StreamHandler()]
    access.propagate = False
    configure_logging()

    access.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:5000", "GET", "/api/x", "1.1", 404)

    entry = json.loads(last_json_line(capsys))
    assert entry["logger"] == "uvicorn.access"
    assert entry["message"] == '127.0.0.1:5000 - "GET /api/x HTTP/1.1" 404'
