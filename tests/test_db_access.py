import time

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from backend.db import connect_postgres, connect_sqlserver

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("statement", [
    "INSERT INTO dbo.locations (location_id, location_name, location_type, address, supports_click_collect, "
    "supports_try_on, ships_to_home) VALUES ('LOC-X', 'x', 'store', 'x', 0, 0, 0)",
    "UPDATE dbo.products SET product_name = product_name WHERE 1 = 0",
    "DELETE FROM dbo.inventory_levels WHERE 1 = 0",
    "CREATE TABLE dbo.should_not_exist (id INT)",
])
def test_the_app_login_cannot_change_client_data(sql_server, statement):
    with sql_server.connect() as connection:
        transaction = connection.begin()
        try:
            with pytest.raises(DBAPIError) as error:
                connection.execute(text(statement))
            assert "permission" in str(error.value).lower()
        finally:
            transaction.rollback()


def test_the_app_login_cannot_read_password_hashes(sql_server):
    # every login may see the names of itself and sa (SQL Server's default); the hashes stay hidden
    with sql_server.connect() as connection:
        hashes = connection.execute(text("SELECT password_hash FROM sys.sql_logins")).scalars().all()
    assert hashes
    assert all(value is None for value in hashes)


def test_postgres_cancels_a_query_that_runs_too_long(settings, test_database_name, test_database):
    engine = connect_postgres.postgres_engine(test_database_name, settings=settings.model_copy(
        update={"db_query_timeout_seconds": 1}))
    started = time.monotonic()
    with engine.connect() as connection, pytest.raises(DBAPIError):
        connection.execute(text("SELECT pg_sleep(30)"))
    engine.dispose()
    assert time.monotonic() - started < 10  # far below the 30 s the query would take


def test_sql_server_cancels_a_query_that_runs_too_long(settings):
    engine = connect_sqlserver.sqlserver_engine(settings=settings.model_copy(update={"db_query_timeout_seconds": 1}))
    started = time.monotonic()
    with engine.connect() as connection, pytest.raises(DBAPIError):
        connection.execute(text("WAITFOR DELAY '00:00:30'"))
    engine.dispose()
    assert time.monotonic() - started < 10  # far below the 30 s the query would take


def test_health_of_both_databases(real_database, sql_server):
    assert connect_postgres.health(real_database).ok
    assert connect_sqlserver.health(sql_server).ok


@pytest.mark.parametrize("module, make_engine", [
    (connect_postgres, lambda unreachable: connect_postgres.postgres_engine(settings=unreachable)),
    (connect_sqlserver, lambda unreachable: connect_sqlserver.sqlserver_engine(settings=unreachable)),
])
def test_health_reports_an_unreachable_database_without_raising(settings, module, make_engine):
    unreachable = settings.model_copy(update={"postgres_port": 1, "sql_server_port": 1, "db_query_timeout_seconds": 1})
    engine = make_engine(unreachable)
    result = module.health(engine)
    engine.dispose()
    assert not result.ok
    assert result.error
