from pathlib import Path
import re
import sys

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from backend.db.repositories import catalogue, inventory, locations, staff
from backend.db.connect_sqlserver import sqlserver_engine
from config import get_settings

CLIENT_TABLES = tuple(dict.fromkeys(
    table for repository in (catalogue, inventory, locations, staff) for table in repository.TABLES
))
LOGIN_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,63}")
FORBIDDEN_PASSWORD_CHARACTERS = ";{}'\""

EXISTING_TABLES = "SELECT name FROM sys.tables WHERE schema_id = SCHEMA_ID('dbo')"
LOGIN_EXISTS = "SELECT COUNT(*) FROM sys.server_principals WHERE name = :login"
USER_EXISTS = "SELECT COUNT(*) FROM sys.database_principals WHERE name = :login"
PERMISSION_COUNTS = """
    SELECT COALESCE(SUM(CASE WHEN p.permission_name = 'SELECT' AND p.state = 'G' THEN 1 END), 0),
           COALESCE(SUM(CASE WHEN p.permission_name NOT IN ('SELECT', 'CONNECT') THEN 1 END), 0)
    FROM sys.database_permissions p
    JOIN sys.database_principals u ON u.principal_id = p.grantee_principal_id
    WHERE u.name = :login
"""


class ProvisioningError(Exception):
    """The login could not be set up safely. The message never contains the password."""


class LoginProvisioner:
    """Sets up one read-only login on an autocommit admin connection (CREATE LOGIN cannot run in a transaction)."""

    def __init__(self, connection, login: str):
        self.connection = connection
        self.login = login

    @staticmethod
    def validate_credentials(login: str, password: str):
        # CREATE LOGIN cannot take bound parameters, so both values go into the statement and are checked first
        if not LOGIN_NAME.fullmatch(login):
            raise ProvisioningError("SQL_APP_USER: 3 to 64 letters, digits or underscores, starting with a letter")
        if any(character in password for character in FORBIDDEN_PASSWORD_CHARACTERS):
            raise ProvisioningError("SQL_APP_PASSWORD must not contain ; { } ' or \"")

    def validate_tables_exist(self, tables):
        existing = set(self.connection.execute(text(EXISTING_TABLES)).scalars())
        missing = [table for table in tables if table not in existing]
        if missing:
            raise ProvisioningError(f"Missing tables: {', '.join(missing)}. Run scripts/ingest_input_data.py first.")

    def create_or_update_login(self, password: str) -> str:
        if self._value(LOGIN_EXISTS):
            self._run_with_password(f"ALTER LOGIN [{self.login}] WITH PASSWORD = N'{password}'")
            return "updated"
        self._run_with_password(f"CREATE LOGIN [{self.login}] WITH PASSWORD = N'{password}', "
                                "CHECK_POLICY = ON, DEFAULT_DATABASE = [master]")
        return "created"

    def create_user_if_missing(self):
        if not self._value(USER_EXISTS):
            self.connection.execute(text(f"CREATE USER [{self.login}] FOR LOGIN [{self.login}]"))

    def grant_select(self, tables):
        for table in tables:
            self.connection.execute(text(f"GRANT SELECT ON dbo.[{table}] TO [{self.login}]"))

    def count_permissions(self) -> tuple[int, int]:
        granted, other = self.connection.execute(text(PERMISSION_COUNTS), {"login": self.login}).one()
        return granted, other

    def _value(self, sql):
        return self.connection.execute(text(sql), {"login": self.login}).scalar()

    def _run_with_password(self, statement):
        # on failure SQLAlchemy's error repeats the statement, and so the password; only SQL Server's reason is kept
        try:
            self.connection.execute(text(statement))
        except DBAPIError as error:
            reason = error.orig.args[-1] if error.orig is not None and error.orig.args else type(error).__name__
            raise ProvisioningError(f"SQL Server refused the login change: {reason}") from None


def main():
    settings = get_settings()
    login, password = settings.sql_app_user, settings.sql_app_password.get_secret_value()
    LoginProvisioner.validate_credentials(login, password)

    print("Connecting to SQL Server as the admin login...")
    engine = sqlserver_engine(admin=True)
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            provisioner = LoginProvisioner(connection, login)
            provisioner.validate_tables_exist(CLIENT_TABLES)
            print(f"Login {login}: {provisioner.create_or_update_login(password)}")
            provisioner.create_user_if_missing()
            provisioner.grant_select(CLIENT_TABLES)
            granted, other = provisioner.count_permissions()
    finally:
        engine.dispose()

    print(f"SELECT granted on {granted} tables; other permissions: {other}")
    if granted != len(CLIENT_TABLES) or other:
        raise ProvisioningError("Permissions are not exactly SELECT on the client tables; check them before use.")
    print("Done.")


if __name__ == "__main__":
    try:
        main()
    except ProvisioningError as error:
        raise SystemExit(str(error)) from None
