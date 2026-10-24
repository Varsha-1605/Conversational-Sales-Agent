from pathlib import Path
from datetime import date
from decimal import Decimal
import argparse
import sys
import pandas as pd
from sqlalchemy import Integer, Boolean, Date, Numeric, inspect, select, func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from backend.db.connect_postgres import postgres_engine
from backend.db.schema import (
    customers, purchase_history, journeys, test_payment_methods, AGE_BANDS, LOYALTY_TIERS, CHANNELS,
)

developer_dir = project_root / "data" / "developers"
client_dir = project_root / "data" / "raw"
fixtures_dir = project_root / "fixtures"

parser = argparse.ArgumentParser(description="Load the developer-side data (customers, purchase history, "
                                             "test payment methods) into PostgreSQL.")
parser.add_argument("--dry-run", action="store_true",
                    help="read and check the files only; do not connect to the database")
args = parser.parse_args()

# 1. The tables come from backend/db/schema.py and are created by the Alembic migrations (alembic upgrade head)
tables = [customers, purchase_history, test_payment_methods] # customers first, the others refer to it
SOURCES = {"customers": developer_dir, "purchase_history": developer_dir,
           "test_payment_methods": fixtures_dir} # DATA_SPEC section 5 fixtures (DB-34)

ENUMS = {"age_band": AGE_BANDS, "loyalty_tier": LOYALTY_TIERS, "device_preference": CHANNELS}

# columns that are not in the file (they get their defaults) and columns in the file that are not loaded
NOT_IN_FILE = {"customers": {"password_hash", "save_preferences_allowed", "saved_preferences"}}
NOT_LOADED = {"customers": {"password"}} # plain-text demo password: hashing is a separate piece of work


# 2. Read each CSV as text, then convert every value to its column type
def convert(column, value):
    if value in ("", "null"):
        return None
    if isinstance(column.type, Integer):
        return int(value)
    if isinstance(column.type, Boolean):
        if value not in ("True", "False"):
            raise ValueError(f"expected True or False, got {value!r}")
        return value == "True"
    if isinstance(column.type, Date):
        return date.fromisoformat(value)
    if isinstance(column.type, Numeric):
        return Decimal(value)
    if column.name in ENUMS and value not in ENUMS[column.name]:
        raise ValueError(f"{value!r} is not one of {ENUMS[column.name]}")
    return value


def read_rows(table):
    csv_path = SOURCES[table.name] / f"{table.name}.csv"
    df = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    loaded = [column for column in table.columns if column.name not in NOT_IN_FILE.get(table.name, set())]
    expected = {column.name for column in loaded} | NOT_LOADED.get(table.name, set())
    if set(df.columns) != expected:
        raise ValueError(f"{csv_path.name}: columns {list(df.columns)} do not match {sorted(expected)}")
    rows = []
    for line, record in enumerate(df.to_dict("records"), start=2): # line 1 is the header
        row = {}
        for column in loaded:
            try:
                row[column.name] = convert(column, record[column.name])
            except (ValueError, ArithmeticError) as error:
                raise ValueError(f"{csv_path.name} line {line}, column {column.name}: {error}") from error
            if row[column.name] is None and not column.nullable and column.server_default is None:
                raise ValueError(f"{csv_path.name} line {line}, column {column.name}: value is required")
        rows.append(row)
    return rows


# checks across files; the database cannot enforce references into SQL Server (DATA_SPEC 1)
def check_references(customer_rows, history_rows, method_rows):
    problems = []
    stores = set(pd.read_csv(client_dir / "locations.csv", dtype=str, keep_default_na=False)
                 .query("location_type == 'store'")["location_id"])
    skus = set(pd.read_csv(client_dir / "product_variants.csv", dtype=str, keep_default_na=False)["sku"])
    staff_emails = {email.lower() for email in
                    pd.read_csv(client_dir / "staff_data.csv", dtype=str, keep_default_na=False)["email"]}
    customer_ids = {row["customer_id"] for row in customer_rows}
    for row in customer_rows:
        if row["preferred_store_id"] not in stores:
            problems.append(f"{row['customer_id']}: preferred_store_id {row['preferred_store_id']} is not a store in locations.csv")
        # an email may belong to a customer or a staff member, never both (AC-22, DB-25); the address itself is not printed
        if row["email"].lower() in staff_emails:
            problems.append(f"{row['customer_id']}: email is also a staff email in staff_data.csv")
    for row in history_rows:
        if row["customer_id"] not in customer_ids:
            problems.append(f"purchase_history: {row['customer_id']} is not in customers.csv")
        if row["sku"] not in skus:
            problems.append(f"purchase_history: {row['sku']} is not in product_variants.csv")
    for row in method_rows:
        if row["customer_id"] is not None and row["customer_id"] not in customer_ids:
            problems.append(f"test_payment_methods: {row['method_ref']} belongs to {row['customer_id']}, who is not in customers.csv")
    if problems:
        raise ValueError("Data checks failed, nothing was loaded:\n  " + "\n  ".join(problems))


# convert everything first, so a bad file stops the run before the database is touched
all_rows = {}
for table in tables:
    all_rows[table.name] = read_rows(table)
    print(f"Read {table.name}.csv: {len(all_rows[table.name])} rows")
check_references(all_rows["customers"], all_rows["purchase_history"], all_rows["test_payment_methods"])

if args.dry_run:
    print("Dry run complete. Nothing was loaded.")
    raise SystemExit(0)

# 3. Connect to PostgreSQL
print("Connecting to PostgreSQL...")
engine = postgres_engine()

inspector = inspect(engine)
missing = [table.name for table in tables + [journeys] if not inspector.has_table(table.name)]
missing += [] if inspector.has_sequence("customer_id_seq") else ["customer_id_seq"]
if missing:
    raise SystemExit(f"Tables not found: {', '.join(missing)}. Run 'alembic upgrade head' first.")

# 4. Add the rows that are not there yet, in one transaction
with engine.begin() as connection:
    for table in tables:
        before = connection.execute(select(func.count()).select_from(table)).scalar_one()
        # rows already there are left alone, so later edits (consent, saved preferences) are not overwritten
        connection.execute(pg_insert(table).on_conflict_do_nothing(), all_rows[table.name])
        after = connection.execute(select(func.count()).select_from(table)).scalar_one()
        print(f"Loaded {table.name}: {after - before} new rows ({after} in total)")

    # one empty journey per customer (DATA_SPEC 4.1, DB-35); sign-up creates the row for new customers
    before = connection.execute(select(func.count()).select_from(journeys)).scalar_one()
    connection.execute(pg_insert(journeys).from_select(["customer_id"], select(customers.c.customer_id))
                       .on_conflict_do_nothing())
    after = connection.execute(select(func.count()).select_from(journeys)).scalar_one()
    print(f"Created journeys: {after - before} new rows ({after} in total)")

    # new sign-ups continue after the highest seeded customer number, even if the seed file grows (DB-36);
    # the sequence only ever moves forward
    highest = connection.execute(text(
        "SELECT max(substring(customer_id FROM 6)::int) FROM customers WHERE customer_id ~ '^CUST-[0-9]+$'"
    )).scalar()
    if highest is not None:
        connection.execute(text(
            "SELECT setval('customer_id_seq', :highest) WHERE :highest >= "
            "(SELECT CASE WHEN is_called THEN last_value ELSE last_value - 1 END FROM customer_id_seq)"
        ), {"highest": highest})

print("Seeding complete!")
