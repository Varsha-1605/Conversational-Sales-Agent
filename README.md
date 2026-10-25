# Conversational Sales Agent

Omnichannel retail sales agent for a fashion retailer. The client's data (catalogue, stock, stores, staff) is read from Microsoft SQL Server; everything the product creates (customers, journeys, orders, agent memory) lives in PostgreSQL.

Planning documents are in `.claude/.plan/` (`IMPLEMENTATION_PLAN.md`, `PROJECT_STRUCTURE.md`, `ARCHITECTURE.md`, `DATABASE.md`).

**Built so far (phases 0 to 5):** the setup and test harness, the data foundation, the FastAPI service with its read-only data access, the domain core (transactions, payment balance, tokens, locks), the Inventory and Loyalty and Offers rule workers, and authentication with sessions, roles and Personalization. See the plan for what comes next.

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

7. **Create the app's PostgreSQL login** (named by `POSTGRES_APP_USER`: it can read and write rows but not change tables). `POSTGRES_USER` stays the owner for migrations, the seed and the tests. Run it after the migrations.

   ```
   uv run python scripts/create_postgres_app_user.py
   ```

8. **Load the developer data** (customers, purchase history, test payment methods; one journey per customer). Passwords are stored only as argon2id hashes; the demo passwords stay in the CSV files.

   ```
   uv run python scripts/seed_developer_data.py
   ```

9. **Before a demo, re-date the stock** so it is not stale. One record (`SKU-4001-L-IVO` at `LOC-S03`) stays stale on purpose.

   ```
   uv run python scripts/redate_inventory.py
   ```

## Run the service

```
uv run uvicorn backend.main:create_app --factory --reload
```

The API is under `/api` (interactive docs at `/api/docs`); pages are served from `frontend/`.

Open the app at `http://localhost:8000`, not `127.0.0.1`: the server only accepts form posts from the origin in `ALLOWED_ORIGIN` (default `http://localhost:8000`). The session cookie is `Secure`, which browsers accept on `localhost`.

## What you can try today

| Page | Address | What it does |
|---|---|---|
| Sign up | `/pages/sign-up/` | Creates a customer account. It does not sign you in; you go to Sign in. |
| Sign in | `/pages/sign-in/` | Email, password and the role to sign in as (Customer by default). Add `?channel=kiosk` for the kiosk layout. |
| Personalization | `/pages/personalization/` | Name, gender, preferred store and the two consent choices. A new customer lands here after their first sign-in. |

The demo accounts are in `data/developers/customers.csv` (customers) and `data/raw/staff_data.csv` (store associates and the administrator); the `password` column holds each demo password. After sign-in, `/api/me` shows who you are. Pages for Home, Products, Cart and the rest arrive in later phases, so a successful sign-in currently ends on a "not found" page.

Browsers show friendly errors under each field; rate limits (5 failed sign-ins per email, 30 per address, 10 sign-ups an hour per address) reset when the server restarts.

## Tests

```
uv run pytest -m "not integration"   # no databases needed
uv run pytest                        # all tests; both containers must be running
```

Integration tests drop and rebuild a separate database (`POSTGRES_TEST_DB`, default `sales_agent_test`) from the migrations and the developer seed on every run. They never change the real database or the client data in SQL Server.
