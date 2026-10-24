from pathlib import Path
from datetime import datetime, time
from decimal import Decimal
import argparse
import sys
import pandas as pd
from sqlalchemy import (
    MetaData, Table, Column, String, Integer, Numeric, Boolean,
    ForeignKey, PrimaryKeyConstraint, CheckConstraint, Index, insert, delete,
)
from sqlalchemy.dialects.mssql import TIME, DATETIMEOFFSET

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from backend.db.connect_sqlserver import sqlserver_engine # settings come from config.py (.env)

data_dir = project_root / "data" / "raw"

parser = argparse.ArgumentParser(description="Load the client data files into SQL Server.")
parser.add_argument("--reset", action="store_true",
                    help="drop and recreate the tables first (use once if an older version of this script created them)")
parser.add_argument("--dry-run", action="store_true",
                    help="read and convert the files only; do not connect to the database")
args = parser.parse_args()

# 1. Table definitions: types and keys match DATA_SPEC section 2 (client data only)
metadata = MetaData(schema="dbo")

Table("products", metadata,
      Column("product_id", String(20), primary_key=True),
      Column("product_name", String(200), nullable=False),
      Column("category", String(100), nullable=False),
      Column("subcategory", String(100), nullable=False),
      Column("description", String(500), nullable=False),
      Column("fabric", String(100)),
      Column("material", String(100)),
      Column("fit", String(100)),
      Column("occasion", String(100)),
      Column("segment", String(100)),
      Column("season_tags", String(200), nullable=False),
      Column("image_urls", String(1000), nullable=False))

Table("locations", metadata,
      Column("location_id", String(20), primary_key=True),
      Column("location_name", String(100), nullable=False),
      Column("location_type", String(20), nullable=False),
      Column("address", String(300), nullable=False),
      Column("supports_click_collect", Boolean, nullable=False),
      Column("supports_try_on", Boolean, nullable=False),
      Column("ships_to_home", Boolean, nullable=False))

Table("product_variants", metadata,
      Column("sku", String(40), primary_key=True),
      Column("product_id", String(20), ForeignKey("dbo.products.product_id"), nullable=False),
      Column("size", String(20), nullable=False),
      Column("color", String(30), nullable=False),
      Column("barcode", String(20), nullable=False, unique=True),
      Column("list_price", Numeric(12, 2), nullable=False),
      # SQL Server does not index foreign-key columns by itself; product pages read variants by product (DB-33)
      Index("ix_product_variants_product_id", "product_id"))

Table("product_recommendations", metadata,
      Column("product_id", String(20), ForeignKey("dbo.products.product_id"), nullable=False),
      Column("recommended_product_id", String(20), ForeignKey("dbo.products.product_id"), nullable=False),
      Column("reason", String(20), nullable=False),
      PrimaryKeyConstraint("product_id", "recommended_product_id"),
      CheckConstraint("product_id <> recommended_product_id"))

Table("location_opening_hours", metadata,
      Column("location_id", String(20), ForeignKey("dbo.locations.location_id"), nullable=False),
      Column("day_of_week", String(3), nullable=False),
      Column("opens_at", TIME(0), nullable=False),
      Column("closes_at", TIME(0), nullable=False),
      PrimaryKeyConstraint("location_id", "day_of_week"),
      CheckConstraint("closes_at > opens_at"))

Table("inventory_levels", metadata,
      Column("sku", String(40), ForeignKey("dbo.product_variants.sku"), nullable=False),
      Column("location_id", String(20), ForeignKey("dbo.locations.location_id"), nullable=False),
      Column("on_hand_qty", Integer, nullable=False),
      Column("last_updated_at", DATETIMEOFFSET, nullable=False),
      PrimaryKeyConstraint("sku", "location_id"),
      CheckConstraint("on_hand_qty >= 0"))

# the password column is still plain text; it becomes password_hash when sign-in is built
Table("staff_data", metadata,
      Column("staff_id", String(20), primary_key=True),
      Column("name", String(100), nullable=False),
      Column("email", String(200), nullable=False, unique=True),
      Column("password", String(100), nullable=False),
      Column("role", String(30), nullable=False),
      Column("location_id", String(20), ForeignKey("dbo.locations.location_id")))


# 2. Read each CSV as text, then convert every value to its column type
def convert(column, value):
    if value in ("", "null"):
        return None
    if isinstance(column.type, Numeric):
        return Decimal(value)
    if isinstance(column.type, Integer):
        return int(value)
    if isinstance(column.type, Boolean):
        if value not in ("True", "False"):
            raise ValueError(f"expected True or False, got {value!r}")
        return value == "True"
    if isinstance(column.type, TIME):
        return datetime.strptime(value, "%H:%M").time()
    if isinstance(column.type, DATETIMEOFFSET):
        return datetime.fromisoformat(value).isoformat(timespec="seconds") # keeps the +05:30 offset
    return value


def read_rows(table):
    csv_path = data_dir / f"{table.name}.csv"
    df = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    if set(df.columns) != set(table.columns.keys()):
        raise ValueError(f"{csv_path.name}: columns {list(df.columns)} do not match {list(table.columns.keys())}")
    rows = []
    for line, record in enumerate(df.to_dict("records"), start=2): # line 1 is the header
        row = {}
        for column in table.columns:
            try:
                row[column.name] = convert(column, record[column.name])
            except (ValueError, ArithmeticError) as error:
                raise ValueError(f"{csv_path.name} line {line}, column {column.name}: {error}") from error
            if row[column.name] is None and not column.nullable:
                raise ValueError(f"{csv_path.name} line {line}, column {column.name}: value is required")
        rows.append(row)
    return rows


# rules between files that a database key cannot express (DATA_SPEC 2.6, 2.7, 2.9)
def check_rules(all_rows):
    problems = []
    days = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
    stores = [row["location_id"] for row in all_rows["locations"] if row["location_type"] == "store"]
    hours = {}
    for row in all_rows["location_opening_hours"]:
        hours.setdefault(row["location_id"], {})[row["day_of_week"]] = row
    associates = {row["location_id"] for row in all_rows["staff_data"] if row["role"] == "store_associate"}
    for store in stores:
        store_hours = hours.get(store, {})
        if set(store_hours) != days:
            problems.append(f"{store}: opening hours must cover mon to sun, found {sorted(store_hours)}")
        sunday = store_hours.get("sun")
        if sunday and (sunday["opens_at"], sunday["closes_at"]) != (time(10, 0), time(17, 0)):
            problems.append(f"{store}: Sunday hours must be 10:00 to 17:00")
        if store not in associates:
            problems.append(f"{store}: no store associate in staff_data.csv")
    if problems:
        raise ValueError("Data checks failed, nothing was loaded:\n  " + "\n  ".join(problems))


# convert everything first, so a bad file stops the run before the database is touched
all_rows = {}
for table in metadata.sorted_tables:
    all_rows[table.name] = read_rows(table)
    print(f"Read {table.name}.csv: {len(all_rows[table.name])} rows")
check_rules(all_rows)

if args.dry_run:
    print("Dry run complete. Nothing was loaded.")
    raise SystemExit(0)

# 3. Connect to Docker MSSQL Server with the admin login (the app itself only ever has the read-only login)
print("Connecting to MSSQL Database...")
engine = sqlserver_engine(admin=True)

# 4. Create the tables if they are missing, then replace their rows in one transaction
if args.reset:
    print("Dropping the tables...")
    metadata.drop_all(engine)
metadata.create_all(engine)

with engine.begin() as connection:
    for table in reversed(metadata.sorted_tables): # children before parents
        connection.execute(delete(table))
    for table in metadata.sorted_tables: # parents before children
        print(f"Loading {table.name}...")
        connection.execute(insert(table), all_rows[table.name])

print("Data ingestion complete!")
