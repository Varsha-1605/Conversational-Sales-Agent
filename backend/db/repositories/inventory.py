from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import bindparam, text

from backend.db.connect_sqlserver import from_sql_server
from backend.db.health import check

# Read-only access to the client's stock records (DB-40). These are on-hand quantities as recorded;
# availability (holds subtracted, staleness applied) is decided by the Inventory agent, not here.
TABLES = ("inventory_levels",)

_COLUMNS = "sku, location_id, on_hand_qty, last_updated_at"


@dataclass(frozen=True)
class StockRecord:
    sku: str
    location_id: str
    on_hand_qty: int
    last_updated_at: datetime  # in IST


def _record(row):
    return StockRecord(**{**row, "last_updated_at": from_sql_server(row["last_updated_at"])})


def stock(connection, sku, location_id):
    row = connection.execute(
        text(f"SELECT {_COLUMNS} FROM dbo.inventory_levels WHERE sku = :sku AND location_id = :location_id"),
        {"sku": sku, "location_id": location_id},
    ).mappings().first()
    return _record(row) if row else None


def stock_for(connection, skus, location_id=None):
    """Stock records for the SKUs, at every location or at one."""
    wanted = list(dict.fromkeys(skus))
    if not wanted:
        return []
    sql = f"SELECT {_COLUMNS} FROM dbo.inventory_levels WHERE sku IN :skus"
    parameters = {"skus": wanted}
    if location_id is not None:
        sql += " AND location_id = :location_id"
        parameters["location_id"] = location_id
    statement = text(sql + " ORDER BY sku, location_id").bindparams(bindparam("skus", expanding=True))
    return [_record(row) for row in connection.execute(statement, parameters).mappings()]


def health(engine):
    return check(engine, *(f"SELECT TOP 1 1 FROM dbo.{table}" for table in TABLES))
