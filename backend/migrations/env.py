from logging.config import fileConfig
from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool
from backend.db.connect_postgres import postgres_url
from backend.db.schema import metadata

config = context.config
fileConfig(config.config_file_name)

# Alembic compares the database with these table definitions
target_metadata = metadata


def run_migrations_offline():
    # writes the SQL instead of running it (alembic upgrade head --sql)
    context.configure(
        url=postgres_url().render_as_string(hide_password=False),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    # one plain connection: a migration must not be cut off by the app's per-statement time limit
    with create_engine(postgres_url(), poolclass=NullPool).connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
