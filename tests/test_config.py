import pytest
from pydantic import ValidationError

from config import Settings

REQUIRED = {
    "POSTGRES_DB": "sales",
    "POSTGRES_USER": "user",
    "POSTGRES_PASSWORD": "pg-secret-1",
    "SQL_APP_USER": "app_reader",
    "SQL_APP_PASSWORD": "sql-secret-2",
    "TOKEN_SIGNING_KEY": "token-secret-3-" + "x" * 32,
}
OPTIONAL = [
    "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_TEST_DB", "SQL_SERVER_HOST", "SQL_SERVER_PORT", "SQL_ODBC_DRIVER",
    "SQL_ADMIN_USER", "SQL_ADMIN_PASSWORD", "ALLOWED_ORIGIN", "DB_QUERY_TIMEOUT_SECONDS", "DB_POOL_SIZE",
    "DB_MAX_OVERFLOW", "ACTION_TOKEN_MINUTES", "LINK_TOKEN_MINUTES",
]


@pytest.fixture
def environment(monkeypatch):
    # start from an environment with only the required variables; .env is not read (_env_file=None)
    for name in OPTIONAL:
        monkeypatch.delenv(name, raising=False)
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)
    return monkeypatch


def test_missing_variable_is_named_without_any_value(environment):
    environment.delenv("POSTGRES_DB")
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None)
    message = str(error.value)
    assert "postgres_db" in message
    assert "pg-secret-1" not in message
    assert "sql-secret-2" not in message


def test_the_app_needs_its_read_only_login(environment):
    environment.delenv("SQL_APP_PASSWORD")
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None)
    assert "sql_app_password" in str(error.value)


def test_the_admin_login_is_optional(environment):
    settings = Settings(_env_file=None)
    assert settings.sql_admin_user is None
    assert settings.sql_admin_password is None


def test_invalid_port_is_named_without_its_value(environment):
    environment.setenv("POSTGRES_PORT", "not-a-port")
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None)
    assert "postgres_port" in str(error.value)
    assert "not-a-port" not in str(error.value)


def test_defaults(environment):
    settings = Settings(_env_file=None)
    assert settings.postgres_host == "localhost"
    assert settings.postgres_port == 5432
    assert settings.sql_server_port == 1433
    assert settings.sql_odbc_driver == "SQL Server"
    assert settings.allowed_origin == "http://localhost:8000"
    assert (settings.db_query_timeout_seconds, settings.db_pool_size, settings.db_max_overflow) == (5, 5, 10)
    assert (settings.action_token_minutes, settings.link_token_minutes) == (10, 15)
    assert settings.test_database == "sales_test"


def test_a_short_token_key_is_refused_without_its_value(environment):
    environment.setenv("TOKEN_SIGNING_KEY", "short-secret-4")
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None)
    assert "token_signing_key" in str(error.value)
    assert "short-secret-4" not in str(error.value)


def test_test_database_can_be_named(environment):
    environment.setenv("POSTGRES_TEST_DB", "other_test")
    assert Settings(_env_file=None).test_database == "other_test"


@pytest.mark.parametrize("name", ["postgres", "sales", "Sales_Test", "sales_test; drop", "test", 'x"_test'])
def test_a_name_that_is_not_clearly_a_test_database_is_refused(environment, name):
    # the test harness drops this database on every run, so a real database's name must never pass
    environment.setenv("POSTGRES_TEST_DB", name)
    with pytest.raises(ValueError):
        Settings(_env_file=None).test_database


def test_test_database_must_differ_from_the_real_one(environment):
    environment.setenv("POSTGRES_DB", "sales_test")
    environment.setenv("POSTGRES_TEST_DB", "sales_test")
    with pytest.raises(ValueError):
        Settings(_env_file=None).test_database


def test_passwords_are_not_printed(environment):
    environment.setenv("SQL_ADMIN_PASSWORD", "sql-admin-secret-3")
    settings = Settings(_env_file=None)
    for text in (repr(settings), str(settings)):
        assert "pg-secret-1" not in text
        assert "sql-secret-2" not in text
        assert "sql-admin-secret-3" not in text
        assert REQUIRED["TOKEN_SIGNING_KEY"] not in text
