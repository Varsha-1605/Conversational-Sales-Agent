from pathlib import Path
from datetime import datetime, timedelta, timezone
import argparse
import urllib.parse
import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

# __file__ is 'scripts/redate_inventory.py'
script_dir = Path(__file__).resolve().parent # points to scripts
project_root = script_dir.parents[0] # climbs up 1 level to project root

load_dotenv(project_root / ".env")

# The one record that stays stale on purpose, so the "Availability not confirmed" case can be shown (DATA_SPEC 2.8)
STALE_SKU = "SKU-4001-L-IVO"
STALE_LOCATION = "LOC-S03"
STALE_AGE_DAYS = 38 # older than the 30-day limit (DR-08)

parser = argparse.ArgumentParser(description="Move the stock dates forward so demo data is not stale.")
parser.add_argument("--dry-run", action="store_true",
                    help="print the dates that would be set; do not connect to the database")
args = parser.parse_args()

ist = timezone(timedelta(hours=5, minutes=30))
now = datetime.now(ist).replace(microsecond=0)
fresh = now.isoformat() # every record except the stale one
stale = (now - timedelta(days=STALE_AGE_DAYS)).isoformat()

print(f"Fresh records will be dated {fresh}")
print(f"{STALE_SKU} at {STALE_LOCATION} will be dated {stale} ({STALE_AGE_DAYS} days old)")

if args.dry_run:
    print("Dry run complete. Nothing was changed.")
    raise SystemExit(0)

db_host = os.getenv("SQL_SERVER_HOST", "localhost")
db_port = os.getenv("SQL_SERVER_PORT", "1433")
db_user = os.getenv("SQL_ADMIN_USER")
db_password = os.getenv("SQL_ADMIN_PASSWORD")

# 1. Connect to Docker MSSQL Server
print("Connecting to MSSQL Database...")
connection_string = (
        f"DRIVER={{SQL Server}};"
        f"SERVER={db_host},{db_port};"
        f"DATABASE=master;"
        f"UID={db_user};"
        f"PWD={db_password};"
        f"Encrypt=no;"
        f"TrustServerCertificate=yes;"
    )

params = urllib.parse.quote_plus(connection_string)

engine = create_engine(
    f"mssql+pyodbc:///?odbc_connect={params}",
    use_setinputsizes=False,
)

# 2. Update the dates in one transaction
with engine.begin() as connection:
    connection.execute(
        text("UPDATE dbo.inventory_levels SET last_updated_at = CAST(:fresh AS DATETIMEOFFSET) "
             "WHERE NOT (sku = :sku AND location_id = :location)"),
        {"fresh": fresh, "sku": STALE_SKU, "location": STALE_LOCATION},
    )
    connection.execute(
        text("UPDATE dbo.inventory_levels SET last_updated_at = CAST(:stale AS DATETIMEOFFSET) "
             "WHERE sku = :sku AND location_id = :location"),
        {"stale": stale, "sku": STALE_SKU, "location": STALE_LOCATION},
    )
    stale_count = connection.execute(
        text("SELECT COUNT(*) FROM dbo.inventory_levels "
             "WHERE last_updated_at < DATEADD(day, -30, SYSDATETIMEOFFSET())")
    ).scalar_one()

print(f"Done. Records older than 30 days: {stale_count} (expected 1)")
