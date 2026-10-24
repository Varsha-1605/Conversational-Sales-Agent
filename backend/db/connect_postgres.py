from sqlalchemy import create_engine
from sqlalchemy.engine import URL

from backend.db.health import check
from config import get_settings


def postgres_url(database=None, settings=None):
    settings = settings or get_settings()
    return URL.create(
        "postgresql+psycopg",
        username=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=database or settings.postgres_db,
    )


def postgres_engine(database=None, settings=None):
    """Pooled engine for runtime work. Connecting and every statement are time-limited (DB-50, IC-03)."""
    settings = settings or get_settings()
    timeout = settings.db_query_timeout_seconds
    return create_engine(
        postgres_url(database, settings),
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=timeout,  # waiting for a free connection
        pool_pre_ping=True,  # replace connections the server has closed
        connect_args={"connect_timeout": timeout, "options": f"-c statement_timeout={timeout * 1000}"},
    )


def health(engine):
    return check(engine, "SELECT 1")
