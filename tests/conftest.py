"""Shared test fixtures.

Tests marked `integration` need both containers running. They use a separate PostgreSQL
database (settings.test_database), dropped and rebuilt from the migrations and the
developer seed at the start of each run. The real database and the client data in
SQL Server are never changed by tests: SQL Server is reached only through the app's
read-only login.
"""
import logging
import os
import subprocess
import sys
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, delete, func, select, text
from sqlalchemy.pool import NullPool

from backend.db.connect_postgres import postgres_engine, postgres_url
from backend.db.repositories import inventory
from backend.db.schema import agent_runs, metadata, orders, payment_attempts, refunds, reservations, stock_holds
from backend.db.connect_sqlserver import sqlserver_engine
from backend.logging_setup import SERVER_LOGGERS
from config import PROJECT_ROOT, get_settings


@pytest.fixture
def restore_logging():
    """configure_logging() rewires the root and server loggers; put pytest's setup back afterwards.
    Call configure_logging() inside the test itself, so its handler writes to capsys's stdout."""
    loggers = [logging.getLogger(), *(logging.getLogger(name) for name in SERVER_LOGGERS)]
    saved = [(logger, logger.handlers[:], logger.level, logger.propagate) for logger in loggers]
    yield
    for logger, handlers, level, propagate in saved:
        logger.handlers[:] = handlers
        logger.setLevel(level)
        logger.propagate = propagate


def count_rows(engine):
    with engine.connect() as connection:
        return {
            table.name: connection.execute(select(func.count()).select_from(table)).scalar_one()
            for table in metadata.sorted_tables
        }


def run_on_database(command, database):
    """Run a project command (migration, seed) with POSTGRES_DB pointed at the given database."""
    env = {**os.environ, "POSTGRES_DB": database}  # the migration and the seed script read POSTGRES_DB
    result = subprocess.run([sys.executable, *command], cwd=PROJECT_ROOT, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        pytest.fail(f"{' '.join(command)} failed on {database}:\n{result.stderr[-2000:]}")


@pytest.fixture(scope="session")
def settings():
    return get_settings()


@pytest.fixture(scope="session")
def row_counts():
    return count_rows


@pytest.fixture(scope="session")
def real_database(settings):
    engine = postgres_engine(settings=settings)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def real_counts_before(real_database):
    # taken before the test database is rebuilt, so a test can prove the real one was not touched
    return count_rows(real_database)


@pytest.fixture(scope="session")
def test_database_name(settings):
    try:
        return settings.test_database  # refuses a name that does not look like a test database
    except ValueError as error:
        pytest.fail(str(error))


@pytest.fixture(scope="session")
def test_database(settings, test_database_name, real_counts_before):
    # 1. drop and create the test database (CREATE DATABASE cannot run inside a transaction)
    admin = create_engine(postgres_url(settings=settings), isolation_level="AUTOCOMMIT", poolclass=NullPool)
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{test_database_name}" WITH (FORCE)'))
        connection.execute(text(f'CREATE DATABASE "{test_database_name}"'))
    admin.dispose()

    # 2. build the schema and load the seed with the project's own tools, pointed at the test database
    run_on_database(["-m", "alembic", "upgrade", "head"], test_database_name)
    run_on_database(["scripts/seed_developer_data.py"], test_database_name)

    engine = postgres_engine(test_database_name, settings=settings)
    yield engine
    engine.dispose()


@pytest.fixture
def committing_database(test_database):
    """The test database, for tests whose units of work commit. Afterwards the runtime tables, which the seed leaves
    empty, are emptied again, so later tests still see only the seed. Children are deleted before their parents."""
    yield test_database
    with test_database.begin() as connection:
        for table in (refunds, payment_attempts, stock_holds, reservations, agent_runs, orders):
            connection.execute(delete(table))


@pytest.fixture
def rolled_back_connection(test_database):
    """A connection whose single transaction is rolled back after the test, so nothing it writes is kept."""
    with test_database.connect() as connection:
        transaction = connection.begin()
        yield connection
        transaction.rollback()


@pytest.fixture(scope="session")
def seed_test_database(test_database_name):
    """Re-run the seed on the test database (it adds only missing rows)."""
    return lambda: run_on_database(["scripts/seed_developer_data.py"], test_database_name)


@pytest.fixture(scope="session")
def sql_server(settings):
    # the app's read-only login: tests can only read the client data
    engine = sqlserver_engine(settings=settings)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def stock_data_now(sql_server):
    """An hour after the client's stock was last refreshed. At this moment the seed's current records are fresh and
    its one old record is stale, however long ago the data was loaded or re-dated."""
    with sql_server.connect() as client:
        return inventory.stock(client, "SKU-1001-M-WHI", "LOC-W01").last_updated_at + timedelta(hours=1)
