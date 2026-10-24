from pathlib import Path
from datetime import date
import argparse
import sys
import pandas as pd
from sqlalchemy import Integer, Boolean, Date, inspect, select, func
from sqlalchemy.dialects.postgresql import insert as pg_insert

# __file__ is 'scripts/seed_developer_data.py'
script_dir = Path(__file__).resolve().parent # points to scripts
project_root = script_dir.parents[0] # climbs up 1 level to project root
sys.path.insert(0, str(project_root)) # so that 'backend' can be imported

from backend.db.postgres import postgres_engine
from backend.db.schema import customers, purchase_history, AGE_BANDS, LOYALTY_TIERS, CHANNELS

developer_dir = project_root / "data" / "developers"
client_dir = project_root / "data" / "raw"

parser = argparse.ArgumentParser(description="Load the developer-side data (customers, purchase history) into PostgreSQL.")
parser.add_argument("--dry-run", action="store_true",
                    help="read and check the files only; do not connect to the database")
args = parser.parse_args()

# 1. The tables come from backend/db/schema.py and are created by the Alembic migrations (alembic upgrade head)
tables = [customers, purchase_history] # customers first, purchase_history refers to it

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
    if column.name in ENUMS and value not in ENUMS[column.name]:
        raise ValueError(f"{value!r} is not one of {ENUMS[column.name]}")
    return value


def read_rows(table):
    csv_path = developer_dir / f"{table.name}.csv"
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
def check_references(customer_rows, history_rows):
    problems = []
    stores = set(pd.read_csv(client_dir / "locations.csv", dtype=str, keep_default_na=False)
                 .query("location_type == 'store'")["location_id"])
    skus = set(pd.read_csv(client_dir / "product_variants.csv", dtype=str, keep_default_na=False)["sku"])
    customer_ids = {row["customer_id"] for row in customer_rows}
    for row in customer_rows:
        if row["preferred_store_id"] not in stores:
            problems.append(f"{row['customer_id']}: preferred_store_id {row['preferred_store_id']} is not a store in locations.csv")
    for row in history_rows:
        if row["customer_id"] not in customer_ids:
            problems.append(f"purchase_history: {row['customer_id']} is not in customers.csv")
        if row["sku"] not in skus:
            problems.append(f"purchase_history: {row['sku']} is not in product_variants.csv")
    if problems:
        raise ValueError("Data checks failed, nothing was loaded:\n  " + "\n  ".join(problems))


# convert everything first, so a bad file stops the run before the database is touched
all_rows = {}
for table in tables:
    all_rows[table.name] = read_rows(table)
    print(f"Read {table.name}.csv: {len(all_rows[table.name])} rows")
check_references(all_rows["customers"], all_rows["purchase_history"])

if args.dry_run:
    print("Dry run complete. Nothing was loaded.")
    raise SystemExit(0)

# 3. Connect to PostgreSQL
print("Connecting to PostgreSQL...")
engine = postgres_engine()

missing = [table.name for table in tables if not inspect(engine).has_table(table.name)]
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

print("Seeding complete!")
