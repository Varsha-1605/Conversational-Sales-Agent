from pathlib import Path
from datetime import datetime, timedelta
import argparse
import sys
from sqlalchemy import text

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from backend.db.connect_sqlserver import sqlserver_engine # settings come from config.py (.env)
from backend.domain.clock import IST

# The one record that stays stale on purpose, so the "Availability not confirmed" case can be shown (DATA_SPEC 2.8)
STALE_SKU = "SKU-4001-L-IVO"
STALE_LOCATION = "LOC-S03"
STALE_AGE_DAYS = 38 # older than the 30-day limit (DR-08)

parser = argparse.ArgumentParser(description="Move the stock dates forward so demo data is not stale.")
parser.add_argument("--dry-run", action="store_true",
                    help="print the dates that would be set; do not connect to the database")
args = parser.parse_args()

now = datetime.now(IST).replace(microsecond=0)
fresh = now.isoformat() # every record except the stale one
stale = (now - timedelta(days=STALE_AGE_DAYS)).isoformat()

print(f"Fresh records will be dated {fresh}")
print(f"{STALE_SKU} at {STALE_LOCATION} will be dated {stale} ({STALE_AGE_DAYS} days old)")

if args.dry_run:
    print("Dry run complete. Nothing was changed.")
    raise SystemExit(0)

# 1. Connect to Docker MSSQL Server with the admin login (the app itself only ever has the read-only login)
print("Connecting to MSSQL Database...")
engine = sqlserver_engine(admin=True)

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
