from sqlalchemy import create_engine
from sqlalchemy.engine import URL

from backend.db.health import check
from config import get_settings


def _url(user, password, database, settings):
    return URL.create(
        "postgresql+psycopg",
        username=user,
        password=password.get_secret_value(),
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=database or settings.postgres_db,
    )


def postgres_url(database=None, settings=None):
    """The owner login: migrations, the seed, the setup scripts and tests. The running app does not use it."""
    settings = settings or get_settings()
    return _url(settings.postgres_user, settings.postgres_password, database, settings)


def postgres_app_url(database=None, settings=None):
    """The app's own login: data rights on the tables and nothing else (DB-53)."""
    settings = settings or get_settings()
    return _url(settings.postgres_app_user, settings.postgres_app_password, database, settings)


def _pooled_engine(url, settings):
    timeout = settings.db_query_timeout_seconds
    return create_engine(
        url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=timeout,  # waiting for a free connection
        pool_pre_ping=True,  # replace connections the server has closed
        connect_args={"connect_timeout": timeout, "options": f"-c statement_timeout={timeout * 1000}"},
    )


def postgres_engine(database=None, settings=None):
    """Pooled engine on the owner login, for scripts and tests. Connecting and every statement are time-limited
    (DB-50, IC-03)."""
    settings = settings or get_settings()
    return _pooled_engine(postgres_url(database, settings), settings)


def postgres_app_engine(database=None, settings=None):
    """Pooled engine on the app's login: what the running service uses."""
    settings = settings or get_settings()
    return _pooled_engine(postgres_app_url(database, settings), settings)


def health(engine):
    return check(engine, "SELECT 1")
