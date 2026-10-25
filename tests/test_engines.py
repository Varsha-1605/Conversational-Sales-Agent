import pytest

from backend.db.connect_postgres import postgres_engine
from backend.db.connect_sqlserver import sqlserver_engine
from config import Settings

SECRETS = {"postgres_password": "pg-secret-1", "postgres_app_password": "pg-app-secret-4",
           "sql_app_password": "app-secret-2", "sql_admin_password": "admin-secret-3"}


@pytest.fixture
def settings():
    return Settings(_env_file=None, postgres_db="sales", postgres_user="u", postgres_app_user="app_runtime",
                    sql_app_user="app_reader", sql_admin_user="sa", token_signing_key="k" * 32,
                    session_cookie_key="c" * 32, **SECRETS)


@pytest.mark.parametrize("make", [
    lambda settings: postgres_engine(settings=settings),
    lambda settings: sqlserver_engine(settings=settings),
    lambda settings: sqlserver_engine(admin=True, settings=settings),
])
def test_printing_an_engine_never_shows_a_password(settings, make):
    # an engine is created without connecting; its text appears in tracebacks and test failures
    engine = make(settings)
    shown = f"{engine!r} {engine.url}"
    engine.dispose()
    assert not any(secret in shown for secret in SECRETS.values())
    assert "***" in shown


def test_the_admin_login_is_required_only_when_asked_for(settings):
    without_admin = settings.model_copy(update={"sql_admin_user": None, "sql_admin_password": None})
    sqlserver_engine(settings=without_admin).dispose()
    with pytest.raises(RuntimeError):
        sqlserver_engine(admin=True, settings=without_admin)
