from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# __file__ is 'backend/config.py'
PROJECT_ROOT = Path(__file__).resolve().parents[1]


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
    # the loader scripts' login; the test harness uses it read-only until the app's read-only login exists (DB-06)
    sql_admin_user: str
    sql_admin_password: SecretStr

    @property
    def test_database(self) -> str:
        return self.postgres_test_db or f"{self.postgres_db}_test"


@lru_cache
def get_settings() -> Settings:
    return Settings()
