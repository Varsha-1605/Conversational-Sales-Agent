from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time
from threading import Barrier
from time import sleep

import pytest
from sqlalchemy import func, insert, select, text

from backend.db.repositories import inventory
from backend.db.schema import orders, stock_holds
from backend.domain.clock import IST, FixedClock
from backend.domain.database_locks import lock_customer_coupons, lock_delivery_window, lock_stock
from backend.domain.ids import next_id
from backend.domain.stock_availability import calculate_available_stock
from backend.domain.transaction import open_transaction, write_operation
from tests.sample_rows import hold_row, order_row

pytestmark = pytest.mark.integration

CLOCK = FixedClock(datetime(2026, 10, 24, 12, 0, tzinfo=IST))
# the time between a buyer's check and its write; without the lock the other buyer's check would pass inside it
RACE_WINDOW_SECONDS = 0.3


def count_race_winners(attempt):
    """Run `attempt` in two threads released at the same moment; return how many succeeded."""
    barrier = Barrier(2)

    def contender(_):
        barrier.wait()
        return attempt()

    with ThreadPoolExecutor(max_workers=2) as pool:
        return sum(pool.map(contender, range(2)))


@write_operation
def create_committed_hold(worker_connection, sku, location_id, quantity=1):
    worker_connection.execute(insert(stock_holds).values(
        hold_row(worker_connection.next_id("hold"), sku=sku, location_id=location_id, quantity=quantity)))


def test_two_buyers_of_the_last_unit_exactly_one_succeeds(committing_database, sql_server):
    sku, location_id = "SKU-3001-40-NAV", "LOC-S01"
    with sql_server.connect() as client:
        on_hand = inventory.stock(client, sku, location_id).on_hand_qty
    assert on_hand >= 1
    if on_hand > 1:  # leave exactly one transaction available
        with open_transaction(committing_database, CLOCK) as transaction:
            transaction.run_write_operation(create_committed_hold, sku, location_id, on_hand - 1)

    def buy():
        with open_transaction(committing_database, CLOCK) as transaction, sql_server.connect() as client:
            lock_stock(transaction.connection, [(sku, location_id)])
            if calculate_available_stock(transaction.connection, client, sku, location_id, CLOCK.now()) < 1:
                return False
            sleep(RACE_WINDOW_SECONDS)
            transaction.run_write_operation(create_committed_hold, sku, location_id)
            return True

    assert count_race_winners(buy) == 1
    with committing_database.connect() as connection, sql_server.connect() as client:
        assert calculate_available_stock(connection, client, sku, location_id, CLOCK.now()) == 0


def place_order(transaction, **fields):
    order_id = next_id(transaction.connection, "order")
    transaction.connection.execute(insert(orders).values(order_row(order_id, **fields)))


def test_a_first_order_coupon_is_used_by_only_one_of_two_checkouts(committing_database):
    customer_id = "CUST-0002"  # has no orders in the seed

    def checkout_with_first_order_coupon():
        with open_transaction(committing_database, CLOCK) as transaction:
            lock_customer_coupons(transaction.connection, customer_id)
            prior_orders = transaction.connection.execute(
                select(func.count()).select_from(orders)
                .where(orders.c.customer_id == customer_id, orders.c.order_status != "cancelled")).scalar_one()
            if prior_orders:
                return False
            sleep(RACE_WINDOW_SECONDS)
            place_order(transaction, customer_id=customer_id)
            return True

    assert count_race_winners(checkout_with_first_order_coupon) == 1


DELIVERY_DATE, WINDOW_START = date(2026, 10, 26), time(10, 0)
SLOT = {"date": DELIVERY_DATE.isoformat(), "start": "10:00:00", "end": "14:00:00"}
CAPACITY_PER_WINDOW = 20  # business_rules delivery_slots.capacity_per_window


def orders_in_window(connection):
    return connection.execute(text(
        "SELECT COUNT(*) FROM orders WHERE delivery_slot->>'date' = :date AND delivery_slot->>'start' = :start"
        " AND order_status <> 'cancelled'"), {"date": SLOT["date"], "start": SLOT["start"]}).scalar_one()


def test_two_checkouts_for_the_last_place_in_a_delivery_window_only_one_gets_it(committing_database):
    with open_transaction(committing_database, CLOCK) as transaction:
        for _ in range(CAPACITY_PER_WINDOW - 1):
            place_order(transaction, delivery_slot=SLOT)

    def book_window():
        with open_transaction(committing_database, CLOCK) as transaction:
            lock_delivery_window(transaction.connection, DELIVERY_DATE, WINDOW_START)
            if orders_in_window(transaction.connection) >= CAPACITY_PER_WINDOW:
                return False
            sleep(RACE_WINDOW_SECONDS)
            place_order(transaction, delivery_slot=SLOT)
            return True

    assert count_race_winners(book_window) == 1
    with committing_database.connect() as connection:
        assert orders_in_window(connection) == CAPACITY_PER_WINDOW


def test_locks_are_released_when_the_transaction_ends(committing_database):
    with open_transaction(committing_database, CLOCK) as transaction:
        lock_stock(transaction.connection, [("SKU-2001-9-BRO", "LOC-W01"), ("SKU-1001-M-WHI", "LOC-W01")])
    with committing_database.connect() as connection:
        held = connection.execute(text(
            "SELECT COUNT(*) FROM pg_locks WHERE locktype = 'advisory'"
            " AND database = (SELECT oid FROM pg_database WHERE datname = current_database())")).scalar_one()
    assert held == 0
