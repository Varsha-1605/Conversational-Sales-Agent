from functools import lru_cache
from pathlib import Path
import re

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# __file__ is 'config.py' at the project root: the one place every part of the project reads its settings from
PROJECT_ROOT = Path(__file__).resolve().parent

# a test database is dropped and rebuilt on every test run, so its name must be unmistakably a test database
_TEST_DATABASE_NAME = re.compile(r"[a-z0-9_]+_test")


class Settings(BaseSettings):
    """Settings from the environment, falling back to .env. Field names match the variables (case-insensitive)."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        extra="ignore",  # .env may hold variables used only by other tools
        frozen=True,
        hide_input_in_errors=True,  # a validation error names the variable, never a value
    )

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str
    postgres_test_db: str | None = None  # separate database the tests rebuild on every run
    postgres_user: str
    postgres_password: SecretStr

    sql_server_host: str = "localhost"
    sql_server_port: int = 1433
    sql_odbc_driver: str = "SQL Server"  # the legacy driver; see DATABASE_ISSUES.md DB-05
    # the app's read-only login: SELECT only on the client tables the repositories read (DB-06)
    sql_app_user: str
    sql_app_password: SecretStr
    # the admin login, used only by the loader scripts and scripts/create_sql_logins.py, never by the app
    sql_admin_user: str | None = None
    sql_admin_password: SecretStr | None = None

    # requests that change state must come from this origin (API 2)
    allowed_origin: str = "http://localhost:8000"

    # signs action tokens (confirm buttons) and link tokens (agent hand-off links), API 2; HS256 needs 32+ bytes
    token_signing_key: SecretStr = Field(min_length=32)
    action_token_minutes: int = Field(default=10, ge=1)
    link_token_minutes: int = Field(default=15, ge=1)

    # connection pools and time limits for both databases (DB-50, IC-03)
    db_query_timeout_seconds: int = Field(default=5, ge=1)
    db_pool_size: int = Field(default=5, ge=1)
    db_max_overflow: int = Field(default=10, ge=0)

    @property
    def test_database(self) -> str:
        """The database the tests drop and rebuild. Raises ValueError unless the name is lowercase, ends in
        _test and differs from POSTGRES_DB, so a real database can never be dropped by mistake."""
        name = self.postgres_test_db or f"{self.postgres_db}_test"
        if not _TEST_DATABASE_NAME.fullmatch(name) or name == self.postgres_db:
            raise ValueError(
                "POSTGRES_TEST_DB must use lowercase letters, digits and underscores, end in _test, "
                "and differ from POSTGRES_DB"
            )
        return name


@lru_cache
def get_settings() -> Settings:
    return Settings()
