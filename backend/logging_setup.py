from contextvars import ContextVar
from datetime import datetime
import json
import logging
import sys

from backend.domain.clock import IST

# set once per request (later by the API middleware); every log line in that request carries it (NF-18)
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


def configure_logging(level=logging.INFO):
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


def mask_name(name):
    # "Varsha Dewangan" -> "V*** D***" (design page 16)
    parts = (name or "").split()
    return " ".join(f"{part[0]}***" for part in parts) if parts else "***"


def mask_email(email):
    # "varsha@example.com" -> "v***@example.com"
    local, at, domain = (email or "").partition("@")
    return f"{local[:1]}***@{domain}" if at and local and domain else "***"
