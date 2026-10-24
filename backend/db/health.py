from dataclasses import dataclass
import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Health:
    ok: bool
    error: str | None = None  # the error type only; messages can name hosts and accounts


def check(engine, *statements) -> Health:
    """Run tiny read statements; report the result instead of raising (Integration status, OP-01, DB-49)."""
    try:
        with engine.connect() as connection:
            for statement in statements:
                connection.execute(text(statement))
    except Exception as error:  # a health check must answer, whatever went wrong
        cause = getattr(error, "orig", None) or error
        logger.warning("health check failed: %s", type(cause).__name__, exc_info=True)
        return Health(ok=False, error=type(cause).__name__)
    return Health(ok=True)
