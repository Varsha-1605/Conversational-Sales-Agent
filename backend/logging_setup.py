from contextvars import ContextVar
from datetime import datetime
import json
import logging
import sys

from backend.domain.clock import IST

# set once per request by the API middleware; every log line in that request carries it (NF-18)
trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)


class JsonFormatter(logging.Formatter):
    """One JSON object per line. Personal data must be masked before it is logged (NF-04)."""

    def format(self, record):
        entry = {
            "time": datetime.fromtimestamp(record.created, IST).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "trace_id": trace_id_var.get(),
        }
        if record.exc_info:
            entry["error"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


# the server's own loggers; uvicorn gives them plain-text handlers of their own at start-up
SERVER_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


def configure_logging(level=logging.INFO):
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # send the server's lines through the same JSON handler, so every line in the log has one format
    for name in SERVER_LOGGERS:
        server_logger = logging.getLogger(name)
        server_logger.handlers.clear()
        server_logger.propagate = True


def mask_name(name):
    """A name safe to log: "Varsha Dewangan" -> "V*** D***" (design page 16); empty -> "***"."""
    parts = (name or "").split()
    return " ".join(f"{part[0]}***" for part in parts) if parts else "***"


def mask_email(email):
    """An email safe to log: "varsha@example.com" -> "v***@example.com"; anything else -> "***"."""
    local, at, domain = (email or "").partition("@")
    return f"{local[:1]}***@{domain}" if at and local and domain else "***"
