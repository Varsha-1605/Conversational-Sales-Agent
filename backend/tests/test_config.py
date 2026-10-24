import pytest
from pydantic import ValidationError

from backend.config import Settings

REQUIRED = {
    "POSTGRES_DB": "sales",
    "POSTGRES_USER": "user",
    "POSTGRES_PASSWORD": "pg-secret-1",
    "SQL_ADMIN_USER": "admin",
    "SQL_ADMIN_PASSWORD": "sql-secret-2",
}
OPTIONAL = ["POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_TEST_DB", "SQL_SERVER_HOST", "SQL_SERVER_PORT"]


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
    assert settings.test_database == "sales_test"


def test_test_database_can_be_named(environment):
    environment.setenv("POSTGRES_TEST_DB", "other_test")
    assert Settings(_env_file=None).test_database == "other_test"


def test_passwords_are_not_printed(environment):
    settings = Settings(_env_file=None)
    for text in (repr(settings), str(settings)):
        assert "pg-secret-1" not in text
        assert "sql-secret-2" not in text
