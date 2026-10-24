"""Shared test fixtures.

Tests marked `integration` need both containers running. They use a separate PostgreSQL
database (settings.test_database), dropped and rebuilt from the migrations and the
developer seed at the start of each run. The real database and the client data in
SQL Server are never changed by tests.
"""
import os
import subprocess
import sys
import urllib.parse

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import URL

from config import PROJECT_ROOT, get_settings
from backend.db.schema import metadata


def postgres_url(settings, database):
    return URL.create(
        "postgresql+psycopg",
        username=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=database,
    )


def count_rows(engine):
    with engine.connect() as connection:
        return {
            table.name: connection.execute(select(func.count()).select_from(table)).scalar_one()
            for table in metadata.sorted_tables
        }


@pytest.fixture(scope="session")
def settings():
    return get_settings()


@pytest.fixture(scope="session")
def row_counts():
    return count_rows


@pytest.fixture(scope="session")
def real_database(settings):
    engine = create_engine(postgres_url(settings, settings.postgres_db))
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def real_counts_before(real_database):
    # taken before the test database is rebuilt, so a test can prove the real one was not touched
    return count_rows(real_database)


@pytest.fixture(scope="session")
def test_database(settings, real_counts_before):
    name = settings.test_database
    if name == settings.postgres_db:
        pytest.fail("POSTGRES_TEST_DB must differ from POSTGRES_DB: tests never use the real database")

    # 1. drop and create the test database (CREATE DATABASE cannot run inside a transaction)
    admin = create_engine(postgres_url(settings, settings.postgres_db), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()

    # 2. build the schema and load the seed with the project's own tools, pointed at the test database
    env = {**os.environ, "POSTGRES_DB": name}  # the migration and the seed script read POSTGRES_DB
    for command in (
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        [sys.executable, "scripts/seed_developer_data.py"],
    ):
        result = subprocess.run(command, cwd=PROJECT_ROOT, env=env, capture_output=True, text=True)
        if result.returncode != 0:
            pytest.fail(f"{' '.join(command[1:])} failed on the test database:\n{result.stderr[-2000:]}")

    engine = create_engine(postgres_url(settings, name))
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def sql_server(settings):
    # client data: tests only read it
    params = urllib.parse.quote_plus(
        "DRIVER={SQL Server};"
        f"SERVER={settings.sql_server_host},{settings.sql_server_port};"
        "DATABASE=master;"
        f"UID={settings.sql_admin_user};"
        f"PWD={settings.sql_admin_password.get_secret_value()};"
        "Encrypt=no;TrustServerCertificate=yes;"
    )
    engine = create_engine(f"mssql+pyodbc:///?odbc_connect={params}", use_setinputsizes=False)
    yield engine
    engine.dispose()
