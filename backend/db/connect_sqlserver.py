from contextlib import contextmanager
from datetime import datetime, time
import re

from sqlalchemy import create_engine, event
from sqlalchemy.engine import URL
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from backend.db.health import check
from backend.domain.clock import to_ist
from config import get_settings

# The client tables sit in master.dbo: this SQL Server setup cannot create a separate database (DB-01)
DATABASE = "master"


def sqlserver_url(*, admin=False, settings=None):
    """The password goes in the URL's password field, which SQLAlchemy masks whenever the URL or engine is
    printed; a raw odbc_connect string would show it in plain text in logs and tracebacks."""
    settings = settings or get_settings()
    if admin:
        if not (settings.sql_admin_user and settings.sql_admin_password):
            raise RuntimeError("This command needs SQL_ADMIN_USER and SQL_ADMIN_PASSWORD")
        user, password = settings.sql_admin_user, settings.sql_admin_password
    else:
        user, password = settings.sql_app_user, settings.sql_app_password
    return URL.create(
        "mssql+pyodbc",
        username=user,
        password=password.get_secret_value(),
        host=settings.sql_server_host,
        port=settings.sql_server_port,
        database=DATABASE,
        query={
            "driver": settings.sql_odbc_driver,
            "Encrypt": "no",  # local Docker stand-in; TLS depends on hosting (ARCHITECTURE 12, NF-01)
            "TrustServerCertificate": "yes",
        },
    )


def sqlserver_engine(*, admin=False, settings=None):
    """Pooled engine for the client's SQL Server. The app uses the read-only login (DB-06); admin=True is for
    the loader and login scripts only. Logging in and every query are time-limited (DB-50, IC-03)."""
    settings = settings or get_settings()
    timeout = settings.db_query_timeout_seconds
    engine = create_engine(
        sqlserver_url(admin=admin, settings=settings),
        use_setinputsizes=False,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=timeout,
        pool_pre_ping=True,
        connect_args={"timeout": timeout},  # login timeout
    )

    @event.listens_for(engine, "connect")
    def limit_query_time(dbapi_connection, _record):
        dbapi_connection.timeout = timeout  # pyodbc cancels a statement that runs longer

    return engine


class ClientDatabaseUnavailable(Exception):
    """The client's SQL Server could not be reached. Answered as SERVICE_UNAVAILABLE, not as a server error."""


@contextmanager
def client_connection(engine):
    """A connection to the client's SQL Server. Only the failure to connect becomes ClientDatabaseUnavailable; an
    error inside the block is raised as it is. Open it only when a request needs client data, so a request that does
    not (a customer signing in) keeps working while the client's database is down."""
    try:
        connection = engine.connect()
    except (OperationalError, InterfaceError, PoolTimeoutError) as error:
        raise ClientDatabaseUnavailable from error
    with connection:
        yield connection


def health(engine):
    return check(engine, "SELECT 1")


# The legacy ODBC driver ({SQL Server}, DB-05) does not understand DATETIMEOFFSET or TIME: it returns them as
# text, and a Python datetime sent to it loses its offset. So SQL Server times always cross as text, through
# these functions; with a modern driver they pass real datetime and time values straight through.
_SQL_SERVER_TIME = re.compile(
    r"(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})(?:\.(\d{1,7}))? ([+-]\d{2}:\d{2})"
)


def from_sql_server(value) -> datetime:
    """A DATETIMEOFFSET as returned by SQL Server (text with the legacy driver) -> an aware datetime in IST."""
    if isinstance(value, datetime):
        return to_ist(value)
    match = _SQL_SERVER_TIME.fullmatch(value)
    if match is None:
        raise ValueError("not a SQL Server DATETIMEOFFSET value")
    day, clock_time, fraction, offset = match.groups()
    fraction = (fraction or "").ljust(6, "0")[:6]  # SQL Server keeps 7 digits; Python keeps 6
    return to_ist(datetime.fromisoformat(f"{day}T{clock_time}.{fraction}{offset}"))


def time_from_sql_server(value) -> time:
    """A TIME as returned by SQL Server (text with the legacy driver: '10:00:00' or '10:00:00.0000000')."""
    if isinstance(value, time):
        return value
    clock_time, _, fraction = value.partition(".")
    return time.fromisoformat(f"{clock_time}.{fraction[:6].ljust(6, '0')}" if fraction else clock_time)


def to_sql_server(value: datetime) -> str:
    """An aware datetime -> ISO 8601 text with its offset, for CAST(:value AS DATETIMEOFFSET) in a query."""
    return to_ist(value).isoformat(timespec="microseconds")
