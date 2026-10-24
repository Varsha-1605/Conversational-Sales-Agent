from hashlib import blake2b

from sqlalchemy import func, select

# ARCHITECTURE 5.6, DB-17, DB-29: checks that read and then write shared limits (the last unit in stock, a
# first-order coupon, a delivery window's capacity, an order's refunds) take a PostgreSQL advisory lock first, so
# two transactions cannot both pass the check. pg_advisory_xact_lock is released when the transaction ends, so a
# lock is always taken inside a transaction (transaction.py) and never has to be released by hand.
#
# `connection` is the transaction's connection or a worker's WorkerConnection. Each call locks its keys in a fixed
# (sorted) order, so two transactions locking the same keys cannot deadlock each other.


def _lock_key(namespace, *parts) -> int:
    # the lock functions take one bigint: a stable 64-bit hash of the namespace and the parts
    digest = blake2b("\x1f".join((namespace, *parts)).encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


def _take_locks(connection, keys):
    for key in sorted(set(keys)):
        connection.execute(select(func.pg_advisory_xact_lock(key)))


def lock_stock(connection, items):
    """Lock (sku, location_id) pairs before reading availability and creating holds (IN-04, DR-09)."""
    _take_locks(connection, (_lock_key("stock", sku, location_id) for sku, location_id in items))


def lock_customer_coupons(connection, customer_id):
    """Lock a customer's coupon use, so a first-order coupon cannot be used by two checkouts at once."""
    _take_locks(connection, [_lock_key("coupon", customer_id)])


def lock_delivery_window(connection, slot_date, window_start):
    """Lock one delivery window (a date and its start time) before counting its orders against capacity."""
    _take_locks(connection, [_lock_key("delivery-window", slot_date.isoformat(), window_start.isoformat())])


def lock_settlement(connection, order_id=None, checkout_key=None):
    """Lock one settlement scope (an order, or a checkout with no order) before its refunds are worked out, so two
    causes settled at once cannot both refund the same money."""
    _take_locks(connection, [_lock_key("settlement", order_id or "", checkout_key or "")])
