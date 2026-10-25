import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from backend.db.connect_postgres import health, postgres_app_engine
from scripts.create_postgres_app_user import AppUserProvisioner, ProvisioningError
from tests.conftest import run_on_database

PASSWORD = "a-long-enough-password"
INSUFFICIENT_PRIVILEGE = "42501"  # PostgreSQL's error code for a right the login does not have


def test_accepts_a_safe_login_and_password():
    AppUserProvisioner.validate_credentials("app_runtime", PASSWORD)


@pytest.mark.parametrize("role", ["ab", "1app", "App", "app runtime", "app-runtime", 'app"; DROP ROLE x; --', "a" * 64])
def test_refuses_a_login_name_that_is_not_a_plain_identifier(role):
    with pytest.raises(ProvisioningError):
        AppUserProvisioner.validate_credentials(role, PASSWORD)


def test_refuses_a_short_password_without_showing_it():
    with pytest.raises(ProvisioningError) as error:
        AppUserProvisioner.validate_credentials("app_runtime", "short")
    assert "short" not in str(error.value)


@pytest.fixture
def app_engine(settings, test_database, test_database_name):
    engine = postgres_app_engine(test_database_name, settings=settings)
    yield engine
    engine.dispose()


@pytest.mark.integration
def test_the_app_login_reads_writes_and_takes_generated_ids(app_engine):
    with app_engine.begin() as connection:
        assert connection.execute(text("SELECT count(*) FROM customers")).scalar_one() >= 12
        connection.execute(text(
            "INSERT INTO sessions (session_id, subject_id, role, channel, created_at, last_activity_at) "
            "VALUES ('S-APP-TEST', 'CUST-0001', 'customer', 'web', now(), now())"))
        connection.execute(text("UPDATE sessions SET ended_at = now() WHERE session_id = 'S-APP-TEST'"))
        connection.execute(text("DELETE FROM sessions WHERE session_id = 'S-APP-TEST'"))
        assert connection.execute(text("SELECT nextval('hold_id_seq')")).scalar_one() >= 1


@pytest.mark.integration
@pytest.mark.parametrize("statement", [
    "CREATE TABLE should_not_exist (id int)",
    "DROP TABLE journeys",
    "ALTER TABLE customers ADD COLUMN should_not_exist int",
    "TRUNCATE sessions",
    "SELECT * FROM alembic_version",
    "UPDATE alembic_version SET version_num = 'x'",
    "CREATE ROLE should_not_exist",
])
def test_the_app_login_cannot_change_tables_or_the_migration_record(app_engine, statement):
    with app_engine.connect() as connection:
        with pytest.raises(DBAPIError) as error:
            connection.execute(text(statement))
        assert error.value.orig.sqlstate == INSUFFICIENT_PRIVILEGE


@pytest.mark.integration
def test_the_app_login_is_a_plain_login(app_engine):
    with app_engine.connect() as connection:
        flags = connection.execute(text(
            "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication FROM pg_roles WHERE rolname = current_user"
        )).one()
    assert flags == (False, False, False, False)


@pytest.mark.integration
def test_the_app_engine_reports_healthy(app_engine):
    assert health(app_engine).ok


@pytest.mark.integration
def test_running_the_setup_again_keeps_the_same_rights(app_engine, test_database_name):
    run_on_database(["scripts/create_postgres_app_user.py"], test_database_name)
    with app_engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM journeys")).scalar_one() >= 12
        with pytest.raises(DBAPIError):
            connection.execute(text("DROP TABLE journeys"))


@pytest.mark.integration
def test_a_table_made_by_a_later_migration_is_covered_by_the_default_rights(
        app_engine, test_database, test_database_name):
    with test_database.begin() as connection:  # the owner login makes a table, as a migration would
        connection.execute(text("CREATE TABLE later_migration_table (id int)"))
    try:
        with app_engine.begin() as connection:
            connection.execute(text("INSERT INTO later_migration_table VALUES (1)"))
            assert connection.execute(text("SELECT count(*) FROM later_migration_table")).scalar_one() == 1
    finally:
        with test_database.begin() as connection:
            connection.execute(text("DROP TABLE later_migration_table"))
