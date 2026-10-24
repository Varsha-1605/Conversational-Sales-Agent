import csv
from datetime import date, datetime

import pytest
from sqlalchemy import insert

from backend.db.schema import orders
from backend.domain.clock import IST
from backend.domain.customer_purchases import Purchase, calculate_points_balance, load_purchase_history
from config import PROJECT_ROOT
from tests.sample_rows import line, order_row

pytestmark = pytest.mark.integration


def test_points_are_seeded_balance_minus_redeemed_with_cancelled_orders_giving_them_back(rolled_back_connection):
    assert calculate_points_balance(rolled_back_connection, "CUST-0002") == 900  # seeded
    rolled_back_connection.execute(insert(orders), [
        order_row("ORD-P1", points_redeemed=200),
        order_row("ORD-P2", points_redeemed=100, status="delivered"),
        order_row("ORD-P3", points_redeemed=300, status="cancelled"),  # full cancellation returns its points
        order_row("ORD-P4", points_redeemed=500, customer_id="CUST-0009"),
    ])
    assert calculate_points_balance(rolled_back_connection, "CUST-0002") == 600
    assert calculate_points_balance(rolled_back_connection, "CUST-0009") == 1300


def test_unknown_customer_has_no_points(rolled_back_connection):
    with pytest.raises(LookupError):
        calculate_points_balance(rolled_back_connection, "CUST-9999")


def seeded_history(customer_id):
    with open(PROJECT_ROOT / "data" / "developers" / "purchase_history.csv", newline="", encoding="utf-8-sig") as file:
        return [Purchase(row["sku"], date.fromisoformat(row["purchased_on"]), True, None)
                for row in csv.DictReader(file) if row["customer_id"] == customer_id]


def test_purchase_history_is_the_seed_plus_kept_order_lines(rolled_back_connection):
    rolled_back_connection.execute(insert(orders), [
        order_row("ORD-H1", confirmed_at=datetime(2026, 10, 22, 23, 30, tzinfo=IST), lines=[
            line("L1", "SKU-1001-M-WHI"),
            line("L2", "SKU-2001-9-BRO", status="removed"),
            line("L3", "SKU-3001-40-NAV", status="exchanged"),
            line("L4", "SKU-1002-L-BLU", status="returned"),
        ], returns=[{"line_id": "L3", "request_type": "exchange", "exchange_sku": "SKU-3001-42-NAV",
                     "status": "completed"}]),
        order_row("ORD-H2", status="cancelled", lines=[line("L1", "SKU-4001-L-IVO")]),
    ])
    october_22 = date(2026, 10, 22)  # 23:30 IST stays on the 22nd
    from_order = [
        Purchase("SKU-1001-M-WHI", october_22, True, "ORD-H1"),
        Purchase("SKU-3001-40-NAV", october_22, False, "ORD-H1"),
        Purchase("SKU-3001-42-NAV", october_22, True, "ORD-H1"),
        Purchase("SKU-1002-L-BLU", october_22, False, "ORD-H1"),
    ]
    history = load_purchase_history(rolled_back_connection, "CUST-0002")
    assert sorted(history, key=repr) == sorted(seeded_history("CUST-0002") + from_order, key=repr)
    assert [purchase.purchased_on for purchase in history] == sorted(
        (purchase.purchased_on for purchase in history), reverse=True)
