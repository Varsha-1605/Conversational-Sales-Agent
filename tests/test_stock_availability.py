from datetime import datetime, timedelta

import pytest
from sqlalchemy import insert

from backend.db.repositories import inventory
from backend.db.schema import stock_holds
from backend.domain.clock import IST
from backend.domain.stock_availability import calculate_available_stock, count_held_units
from tests.sample_rows import hold_row

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=IST)


def test_available_stock_subtracts_committed_and_unexpired_active_holds(rolled_back_connection, sql_server):
    sku, store = "SKU-2001-9-BRO", "LOC-W01"
    with sql_server.connect() as client:
        on_hand = inventory.stock(client, sku, store).on_hand_qty
    hold = {"sku": sku, "location_id": store}
    rolled_back_connection.execute(insert(stock_holds), [
        hold_row("HLD-C", **hold, quantity=2),
        hold_row("HLD-A", **hold, status="active", reference_type="checkout", expires_at=NOW + timedelta(minutes=5)),
        hold_row("HLD-ARRIVED", **hold, status="active", reference_type="reservation"),  # no expiry: customer arrived
        hold_row("HLD-EXPIRED", **hold, status="active", reference_type="checkout", expires_at=NOW),
        hold_row("HLD-R", **hold, status="released", quantity=3),
        hold_row("HLD-L", **hold, status="lapsed", quantity=3),
        hold_row("HLD-OTHER-STORE", sku=sku, location_id="LOC-S01", quantity=1),
    ])
    assert count_held_units(rolled_back_connection, sku, store, NOW) == 4
    with sql_server.connect() as client:
        assert calculate_available_stock(rolled_back_connection, client, sku, store, NOW) == max(0, on_hand - 4)


def test_available_stock_never_goes_below_zero(rolled_back_connection, sql_server):
    sku, store = "SKU-3001-40-NAV", "LOC-S04"
    rolled_back_connection.execute(insert(stock_holds), hold_row("HLD-BIG", sku=sku, location_id=store, quantity=50))
    with sql_server.connect() as client:
        assert calculate_available_stock(rolled_back_connection, client, sku, store, NOW) == 0


def test_no_stock_record_means_no_availability_figure(rolled_back_connection, sql_server):
    with sql_server.connect() as client:
        assert calculate_available_stock(rolled_back_connection, client, "SKU-2001-9-BRO", "LOC-NOWHERE", NOW) is None
