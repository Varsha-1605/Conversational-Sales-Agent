from datetime import datetime, timedelta

import pytest

from agents.workers.inventory import stock_rules
from backend.domain.clock import IST

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=IST)
FRESH = NOW - timedelta(days=1)


@pytest.mark.parametrize("quantity_left, expected", [
    (0, "out_of_stock"), (1, "low_stock"), (stock_rules.LOW_STOCK_AT_OR_BELOW, "low_stock"),
    (stock_rules.LOW_STOCK_AT_OR_BELOW + 1, "in_stock"), (40, "in_stock"),
])
def test_stock_state_follows_the_quantity_left(quantity_left, expected):
    assert stock_rules.stock_state(quantity_left, FRESH, NOW) == expected


def test_stock_is_stale_only_after_thirty_days():
    assert not stock_rules.is_stale(NOW - timedelta(days=30), NOW)
    assert stock_rules.is_stale(NOW - timedelta(days=30, seconds=1), NOW)


@pytest.mark.parametrize("quantity_left", [0, 1, 40])
def test_stale_stock_is_unconfirmed_whatever_the_quantity(quantity_left):
    assert stock_rules.stock_state(quantity_left, NOW - timedelta(days=31), NOW) == "unconfirmed"


def test_a_checkout_hold_lasts_fifteen_minutes():
    assert stock_rules.hold_expires_at("checkout", None, NOW) == NOW + timedelta(minutes=15)


def test_a_reservation_hold_lasts_until_the_end_of_the_slot():
    slot_end = NOW + timedelta(days=1)
    assert stock_rules.hold_expires_at("reservation", slot_end, NOW) == slot_end


@pytest.mark.parametrize("slot_end", [None, NOW, NOW - timedelta(minutes=1)])
def test_a_reservation_hold_needs_a_slot_end_in_the_future(slot_end):
    with pytest.raises(ValueError):
        stock_rules.hold_expires_at("reservation", slot_end, NOW)


@pytest.mark.parametrize("reference_type", ["order", "exchange"])
def test_holds_for_sold_stock_never_lapse(reference_type):
    assert stock_rules.hold_expires_at(reference_type, None, NOW) is None


@pytest.mark.parametrize("reference_type, expected", [
    ("checkout", "active"), ("reservation", "active"), ("order", "committed"), ("exchange", "committed"),
])
def test_holds_for_sold_stock_start_committed_and_the_rest_active(reference_type, expected):
    assert stock_rules.starting_hold_status(reference_type) == expected

