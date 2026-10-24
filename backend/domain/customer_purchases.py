from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select

from backend.db.schema import customers, orders, purchase_history
from backend.domain.clock import to_ist

# ARCHITECTURE 5.6, DATA_SPEC 4.8: a customer's points balance and purchase history are worked out from the seeded
# values and their orders instead of stored, so they cannot drift.


def calculate_points_balance(connection, customer_id) -> int:
    """Seeded points_balance minus the points redeemed on orders. A fully cancelled order gives its points back
    (business_rules loyalty.points_returned_on); partial returns do not. Raises LookupError for an unknown customer."""
    seeded = connection.execute(
        select(customers.c.points_balance).where(customers.c.customer_id == customer_id)).scalar_one_or_none()
    if seeded is None:
        raise LookupError(f"unknown customer {customer_id}")
    redeemed = connection.execute(
        select(func.coalesce(func.sum(orders.c.points_redeemed), 0))
        .where(orders.c.customer_id == customer_id, orders.c.order_status != "cancelled")
    ).scalar_one()
    return seeded - redeemed


@dataclass(frozen=True)
class Purchase:
    sku: str
    purchased_on: date
    kept: bool              # False for a returned item, or the original of an exchange (a size or fit signal)
    order_id: str | None    # None for the seeded history


def load_purchase_history(connection, customer_id) -> list[Purchase]:
    """Seeded purchase history plus the customer's orders, newest first (DATA_SPEC 4.2, 4.8). Cancelled orders and
    removed lines are not purchases. An exchanged line counts once: the replacement as kept, the original not."""
    seeded = [
        Purchase(sku, purchased_on, kept=True, order_id=None)
        for sku, purchased_on in connection.execute(
            select(purchase_history.c.sku, purchase_history.c.purchased_on)
            .where(purchase_history.c.customer_id == customer_id))
    ]
    from_orders = []
    rows = connection.execute(
        select(orders.c.order_id, orders.c.confirmed_at, orders.c.lines, orders.c.returns)
        .where(orders.c.customer_id == customer_id, orders.c.order_status != "cancelled"))
    for order_id, confirmed_at, lines, returns in rows:
        purchased_on = to_ist(confirmed_at).date()
        replacements = {entry["line_id"]: entry["exchange_sku"] for entry in returns
                        if entry["request_type"] == "exchange" and entry["status"] == "completed"}
        for line in lines:
            status = line["line_status"]
            if status == "removed":
                continue
            from_orders.append(Purchase(line["sku"], purchased_on, kept=status == "active", order_id=order_id))
            if status == "exchanged" and line["line_id"] in replacements:
                from_orders.append(Purchase(replacements[line["line_id"]], purchased_on, kept=True, order_id=order_id))
    return sorted(seeded + from_orders, key=lambda purchase: purchase.purchased_on, reverse=True)
