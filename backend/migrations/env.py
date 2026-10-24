from logging.config import fileConfig
from alembic import context
from backend.db.postgres import postgres_url, postgres_engine
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
    with postgres_engine().connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
