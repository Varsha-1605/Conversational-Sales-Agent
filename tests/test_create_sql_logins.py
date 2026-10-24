import pytest
from sqlalchemy.exc import DBAPIError

from backend.db.repositories import catalogue, inventory, locations, staff
from scripts.create_sql_logins import CLIENT_TABLES, LoginProvisioner, ProvisioningError

PASSWORD = "Str0ng-Passw0rd!"


def test_grants_cover_exactly_the_tables_the_repositories_read():
    read = {table for repository in (catalogue, inventory, locations, staff) for table in repository.TABLES}
    assert set(CLIENT_TABLES) == read
    assert len(CLIENT_TABLES) == len(set(CLIENT_TABLES)) == 7


def test_accepts_a_safe_login_and_password():
    LoginProvisioner.validate_credentials("app_reader", PASSWORD)


@pytest.mark.parametrize("login", ["ab", "1app", "app reader", "app-reader", "app]; DROP LOGIN sa; --", "x" * 65])
def test_refuses_a_login_name_that_could_break_the_statement(login):
    with pytest.raises(ProvisioningError):
        LoginProvisioner.validate_credentials(login, PASSWORD)


@pytest.mark.parametrize("character", list(";{}'\""))
def test_refuses_a_password_that_could_break_the_statement(character):
    password = f"Str0ng{character}Passw0rd!"
    with pytest.raises(ProvisioningError) as error:
        LoginProvisioner.validate_credentials("app_reader", password)
    assert password not in str(error.value)


class RefusingConnection:
    """Stands in for SQL Server refusing a login statement, the way SQLAlchemy reports it."""

    def execute(self, statement, parameters=None):
        sql = str(statement)
        if "sys.server_principals" in sql:
            return CountResult(0)  # no login yet, so the next statement is CREATE LOGIN
        raise DBAPIError(sql, parameters, Exception("[42000] Password validation failed. (15118)"))


class CountResult:
    def __init__(self, count):
        self.count = count

    def scalar(self):
        return self.count


def test_a_refused_login_change_never_shows_the_password():
    with pytest.raises(ProvisioningError) as error:
        LoginProvisioner(RefusingConnection(), "app_reader").create_or_update_login(PASSWORD)
    assert "Password validation failed" in str(error.value)
    assert PASSWORD not in str(error.value)
    assert error.value.__suppress_context__  # the original error, which holds the statement, is not printed
