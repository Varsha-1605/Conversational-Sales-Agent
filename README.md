# Conversational Sales Agent

Omnichannel retail sales agent for a fashion retailer. The client's data (catalogue, stock, stores, staff) is read from Microsoft SQL Server; everything the product creates (customers, journeys, orders, agent memory) lives in PostgreSQL.

Planning documents are in `.claude/.plan/` (`IMPLEMENTATION_PLAN.md`, `PROJECT_STRUCTURE.md`, `ARCHITECTURE.md`, `DATABASE.md`).

## Requirements

- Python 3.13 and [uv](https://docs.astral.sh/uv/)
- Docker
- The Windows ODBC driver `SQL Server` (included with Windows)

## Set up a new machine

1. **Start the two databases.** Replace the values in angle brackets. The named volume keeps PostgreSQL data when the container is removed.

   ```
   docker run -d --name legacy-mssql -e ACCEPT_EULA=Y -e "MSSQL_SA_PASSWORD=<password>" -p 1433:1433 mcr.microsoft.com/mssql/server:2022-latest
   docker run -d --name sales-pg -e POSTGRES_USER=<user> -e POSTGRES_PASSWORD=<password> -e POSTGRES_DB=sales_agent -v sales-pg-data:/var/lib/postgresql/data -p 5432:5432 pgvector/pgvector:pg16
   ```

2. **Settings.** Copy `.env.example` to `.env` and fill in the values. `.env` is git-ignored; never commit it.

3. **Install.**

   ```
   uv sync
   ```

4. **Load the client data into SQL Server** (`--reset` on the first run only).

   ```
   uv run python scripts/ingest_input_data.py --reset
   ```

5. **Create the app's read-only SQL Server login** (a database account named by `SQL_APP_USER`, with `SELECT` on the seven client tables only). Run it again after any `--reset` load, which drops the tables and their grants.

   ```
   uv run python scripts/create_sql_logins.py
   ```

6. **Create the PostgreSQL tables.**

   ```
   uv run alembic upgrade head
   ```

7. **Load the developer data** (customers, purchase history, test payment methods; one journey per customer).

   ```
   uv run python scripts/seed_developer_data.py
   ```

8. **Before a demo, re-date the stock** so it is not stale. One record (`SKU-4001-L-IVO` at `LOC-S03`) stays stale on purpose.

   ```
   uv run python scripts/redate_inventory.py
   ```

## Run the service

```
uv run uvicorn backend.main:create_app --factory --reload
```

The API is under `/api` (interactive docs at `/api/docs`); pages are served from `frontend/` once it exists.

## Tests

```
uv run pytest -m "not integration"   # no databases needed
uv run pytest                        # all tests; both containers must be running
```

Integration tests drop and rebuild a separate database (`POSTGRES_TEST_DB`, default `sales_agent_test`) from the migrations and the developer seed on every run. They never change the real database or the client data in SQL Server.
