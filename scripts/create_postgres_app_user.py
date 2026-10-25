from pathlib import Path
import re
import sys

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from psycopg import sql

from backend.db.connect_postgres import postgres_engine
from config import get_settings

# DB-53: the login the running app uses may read and write rows, and nothing else. The owner login (POSTGRES_USER)
# keeps making and changing tables: migrations, the seed and the tests use it. Run after `alembic upgrade head`;
# it is safe to run again, and tables a later migration creates are covered by the default privileges set here.

ROLE_NAME = re.compile(r"[a-z][a-z0-9_]{2,62}")
MIN_PASSWORD_LENGTH = 12
MIGRATION_TABLE = "alembic_version"  # the app has no business changing which migration the database is at


class ProvisioningError(Exception):
    """The login could not be set up safely. The message never contains the password."""


class AppUserProvisioner:
    """Sets up the app's data-only login on an owner connection."""

    def __init__(self, cursor, role: str, owner: str, database: str):
        self.cursor = cursor
        self.role = sql.Identifier(role)
        self.role_name = role
        self.owner = sql.Identifier(owner)
        self.database = sql.Identifier(database)

    @staticmethod
    def validate_credentials(role: str, password: str):
        if not ROLE_NAME.fullmatch(role):
            raise ProvisioningError("POSTGRES_APP_USER: 3 to 63 lowercase letters, digits or underscores, "
                                    "starting with a letter")
        if len(password) < MIN_PASSWORD_LENGTH:
            raise ProvisioningError(f"POSTGRES_APP_PASSWORD must have at least {MIN_PASSWORD_LENGTH} characters")

    def create_or_update_role(self, password: str) -> str:
        self.cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (self.role_name,))
        exists = self.cursor.fetchone() is not None
        verb = "ALTER" if exists else "CREATE"
        self.cursor.execute(sql.SQL(
            "{verb} ROLE {role} LOGIN PASSWORD {password} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION"
        ).format(verb=sql.SQL(verb), role=self.role, password=sql.Literal(password)))
        return "updated" if exists else "created"

    def grant_data_rights(self):
        statements = [
            "GRANT CONNECT ON DATABASE {database} TO {role}",
            "GRANT USAGE ON SCHEMA public TO {role}",
            "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}",
            "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}",  # nextval() for generated IDs
            # tables and sequences a later migration creates get the same rights
            "ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role}",
            "ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {role}",
        ]
        for statement in statements:
            self.cursor.execute(sql.SQL(statement).format(role=self.role, owner=self.owner, database=self.database))
        self.cursor.execute("SELECT to_regclass(%s) IS NOT NULL", (MIGRATION_TABLE,))
        if self.cursor.fetchone()[0]:
            self.cursor.execute(sql.SQL("REVOKE ALL ON TABLE {table} FROM {role}").format(
                table=sql.Identifier(MIGRATION_TABLE), role=self.role))

    def find_extra_rights(self) -> list[str]:
        """Rights the login must not have: creating objects, or being more than a plain login."""
        problems = []
        self.cursor.execute(
            "SELECT rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication FROM pg_roles WHERE rolname = %s",
            (self.role_name,))
        if self.cursor.fetchone()[0]:
            problems.append("it can create databases or roles")
        self.cursor.execute("SELECT has_schema_privilege(%s, 'public', 'CREATE')", (self.role_name,))
        if self.cursor.fetchone()[0]:
            problems.append("it can create tables in the public schema")
        return problems


def main():
    settings = get_settings()
    role, password = settings.postgres_app_user, settings.postgres_app_password.get_secret_value()
    AppUserProvisioner.validate_credentials(role, password)

    print("Connecting to PostgreSQL as the owner login...")
    engine = postgres_engine()
    connection = engine.raw_connection()
    try:
        with connection.cursor() as cursor:
            provisioner = AppUserProvisioner(cursor, role, settings.postgres_user, settings.postgres_db)
            print(f"Login {role}: {provisioner.create_or_update_role(password)}")
            provisioner.grant_data_rights()
            problems = provisioner.find_extra_rights()
        if problems:
            connection.rollback()
            raise ProvisioningError(f"{role} has rights it must not have: {'; '.join(problems)}.")
        connection.commit()
    finally:
        connection.close()
        engine.dispose()
    print("Done: row rights on every table, nothing else.")


if __name__ == "__main__":
    try:
        main()
    except ProvisioningError as error:
        raise SystemExit(str(error)) from None
