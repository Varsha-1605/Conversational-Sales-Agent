from dataclasses import dataclass
from datetime import time

from sqlalchemy import text

from backend.db.connect_sqlserver import time_from_sql_server
from backend.db.health import check

# Read-only access to the client's stores, warehouse and opening hours (DB-40).
TABLES = ("locations", "location_opening_hours")

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

_COLUMNS = ("location_id, location_name, location_type, address, "
            "supports_click_collect, supports_try_on, ships_to_home")


@dataclass(frozen=True)
class Location:
    location_id: str
    location_name: str
    location_type: str  # store or warehouse
    address: str
    supports_click_collect: bool
    supports_try_on: bool
    ships_to_home: bool


@dataclass(frozen=True)
class OpeningHours:
    location_id: str
    day_of_week: str
    opens_at: time
    closes_at: time


def location(connection, location_id):
    row = connection.execute(
        text(f"SELECT {_COLUMNS} FROM dbo.locations WHERE location_id = :location_id"), {"location_id": location_id}
    ).mappings().first()
    return Location(**row) if row else None


def locations(connection, location_type=None):
    sql = f"SELECT {_COLUMNS} FROM dbo.locations"
    parameters = {}
    if location_type is not None:
        sql += " WHERE location_type = :location_type"
        parameters["location_type"] = location_type
    return [Location(**row) for row in connection.execute(text(sql + " ORDER BY location_id"), parameters).mappings()]


def opening_hours(connection, location_id):
    """The location's week, Monday first."""
    rows = connection.execute(
        text("SELECT location_id, day_of_week, opens_at, closes_at FROM dbo.location_opening_hours "
             "WHERE location_id = :location_id"),
        {"location_id": location_id},
    ).mappings()
    week = [
        OpeningHours(**{**row, "opens_at": time_from_sql_server(row["opens_at"]),
                        "closes_at": time_from_sql_server(row["closes_at"])})
        for row in rows
    ]
    return sorted(week, key=lambda hours: DAYS.index(hours.day_of_week))


def health(engine):
    return check(engine, *(f"SELECT TOP 1 1 FROM dbo.{table}" for table in TABLES))
