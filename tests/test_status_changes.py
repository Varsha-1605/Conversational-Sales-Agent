from datetime import datetime, timedelta

import pytest

from backend.domain.clock import IST
from backend.domain.status_changes import (
    ESCALATION_STATUS, ORDER_STATUS_BY_METHOD, PAYMENT_ATTEMPT_STATUS, REFUND_STATUS, RESERVATION_ITEM_STATUS,
    RESERVATION_STATUS, RETURN_OR_EXCHANGE_STATUS, STOCK_HOLD_STATUS, StatusChangeNotAllowed, order_status_rules,
    require_hold_can_commit,
)

# The allowed changes, written out from DATA_SPEC 4.2 to 4.6, business_rules.json and ARCHITECTURE 5.6. Each set of
# rules must allow exactly these: every listed change passes and every other pair of its statuses is refused.
EXPECTED = {
    "order ship to home": (ORDER_STATUS_BY_METHOD["ship_to_home"], {"placed"}, {
        ("placed", "shipped"), ("shipped", "delivered"), ("placed", "cancelled")}),
    "order click & collect": (ORDER_STATUS_BY_METHOD["click_collect"], {"placed"}, {
        ("placed", "ready_for_pickup"), ("ready_for_pickup", "collected"), ("placed", "item_not_found"),
        ("item_not_found", "placed"), ("placed", "cancelled"), ("ready_for_pickup", "cancelled"),
        ("item_not_found", "cancelled")}),
    "order in store": (ORDER_STATUS_BY_METHOD["in_store"], {"collected"}, set()),
    "return or exchange": (RETURN_OR_EXCHANGE_STATUS, {"requested"}, {
        ("requested", "approved"), ("requested", "refused"), ("approved", "completed")}),
    "reservation": (RESERVATION_STATUS, {"confirmed"}, {
        ("confirmed", "customer_arrived"), ("customer_arrived", "completed"), ("confirmed", "lapsed"),
        ("confirmed", "cancelled")}),
    "reservation item": (RESERVATION_ITEM_STATUS, {"pending"}, {("pending", "prepared"), ("pending", "not_found")}),
    "stock hold": (STOCK_HOLD_STATUS, {"active", "committed"}, {
        ("active", "committed"), ("active", "released"), ("active", "lapsed"), ("committed", "released")}),
    "payment attempt": (PAYMENT_ATTEMPT_STATUS, {"success", "declined", "failed", "timeout"}, {
        ("timeout", "success"), ("timeout", "declined"), ("timeout", "failed")}),
    "refund": (REFUND_STATUS, {"initiated"}, {("initiated", "completed"), ("initiated", "failed")}),
    "escalation": (ESCALATION_STATUS, {"waiting"}, {("waiting", "with_associate"), ("with_associate", "resolved")}),
}


@pytest.mark.parametrize("name", EXPECTED)
def test_exactly_the_documented_changes_are_allowed(name):
    rules, _, allowed = EXPECTED[name]
    for current in rules.statuses:
        for new in rules.statuses:
            if (current, new) in allowed:
                rules.require_allowed_change(current, new)
            else:
                with pytest.raises(StatusChangeNotAllowed):
                    rules.require_allowed_change(current, new)


@pytest.mark.parametrize("name", EXPECTED)
def test_records_are_created_only_with_their_starting_statuses(name):
    rules, starting_statuses, _ = EXPECTED[name]
    for status in rules.statuses:
        if status in starting_statuses:
            rules.require_starting_status(status)
        else:
            with pytest.raises(StatusChangeNotAllowed):
                rules.require_starting_status(status)


def test_refused_change_names_the_record_and_both_statuses():
    with pytest.raises(StatusChangeNotAllowed) as error:
        order_status_rules("ship_to_home").require_allowed_change("delivered", "cancelled")
    assert (error.value.record, error.value.current, error.value.new) == (
        "order (ship to home)", "delivered", "cancelled")


def test_unknown_fulfillment_method_has_no_rules():
    with pytest.raises(KeyError):
        order_status_rules("drone")


def test_unknown_status_is_refused():
    with pytest.raises(StatusChangeNotAllowed):
        REFUND_STATUS.require_allowed_change("initiated", "reversed")


NOW = datetime(2026, 10, 24, 12, 0, tzinfo=IST)


def test_an_active_hold_commits_only_before_it_expires():
    require_hold_can_commit(NOW + timedelta(seconds=1), NOW)
    require_hold_can_commit(None, NOW)  # a hold without an expiry (customer arrived) cannot lapse
    with pytest.raises(StatusChangeNotAllowed):
        require_hold_can_commit(NOW, NOW)
    with pytest.raises(StatusChangeNotAllowed):
        require_hold_can_commit(NOW - timedelta(minutes=1), NOW)
