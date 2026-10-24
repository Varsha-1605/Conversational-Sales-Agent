from sqlalchemy import and_, func, or_, select

from backend.db.repositories import inventory
from backend.db.schema import stock_holds

# DATA_SPEC 4.5 and 4.8, DR-09: available stock is worked out from the client's on-hand quantity and our holds
# instead of stored, so it cannot drift. The two halves live in different databases (DB-17), so any decision taken
# on it (a hold, an order) runs under database_locks.lock_stock.


def count_held_units(connection, sku, location_id, now) -> int:
    """Units of a SKU held at a location: committed holds, and active holds that have not yet expired. An active
    hold past expires_at can never be committed (DATA_SPEC 4.5), so it stops counting before the expiry job marks
    it lapsed."""
    return connection.execute(
        select(func.coalesce(func.sum(stock_holds.c.quantity), 0)).where(
            stock_holds.c.sku == sku, stock_holds.c.location_id == location_id,
            or_(stock_holds.c.hold_status == "committed",
                and_(stock_holds.c.hold_status == "active",
                     or_(stock_holds.c.expires_at.is_(None), stock_holds.c.expires_at > now))),
        )
    ).scalar_one()


def quantity_left_after_holds(postgres, record, now) -> int:
    """A stock record's on-hand quantity minus our holds, never below 0."""
    return max(0, record.on_hand_qty - count_held_units(postgres, record.sku, record.location_id, now))


def calculate_available_stock(postgres, sqlserver, sku, location_id, now) -> int | None:
    """The client's on-hand quantity minus our holds, never below 0. None when the client has no stock record for
    that SKU and location."""
    record = inventory.stock(sqlserver, sku, location_id)
    if record is None:
        return None
    return quantity_left_after_holds(postgres, record, now)
