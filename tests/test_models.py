import json
from decimal import Decimal

import pytest
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import insert, select

from backend.db.schema import journeys, orders, reservations
from backend.domain.models import (
    AppliedOffer, CartLine, Change, Escalation, Feedback, OrderLine, OrderStatusEntry, ReservationItem,
    ReservationStatusEntry, ReturnEntry, ShippingAddress, TimeSlot,
)

# The worked examples in DATA_SPEC 4.1, 4.2 and 4.6, exactly as written there
ORDER = json.loads("""{
 "delivery_slot":{"date":"2026-10-26","start":"10:00","end":"14:00"},
 "shipping_address":{"line1":"12 Test Lane","city":"Bengaluru","pincode":"560038"},
 "lines":[{"line_id":"L1","sku":"SKU-1001-M-WHI","quantity":1,"unit_price":"1999.00","line_status":"removed"},{"line_id":"L2","sku":"SKU-2001-9-BRO","quantity":1,"unit_price":"4499.00","line_status":"active"}],
 "applied_offers":[{"rule_id":"PROMO-FEST-500","rule_description":"Festive offer: Rs 500 off carts of Rs 3,000 or more","coupon_code":null,"discount_amount":"500.00"},{"rule_id":"WELCOME300","rule_description":"Rs 300 off a first order of Rs 1,500 or more","coupon_code":"WELCOME300","discount_amount":"300.00"}],
 "status_history":[{"status":"placed","at":"2026-10-22T19:30+05:30","actor_type":"agent","actor_id":"sales-agent","trigger":"customer_request"}],
 "changes":[{"change_id":"CHG-41","type":"remove_item","line_id":"L1","status":"applied","reason":null,"totals_before":{"subtotal":"6498.00","discount_total":"800.00","points_value":"100.00","final_total":"5598.00"},"totals_after":{"subtotal":"4499.00","discount_total":"800.00","points_value":"100.00","final_total":"3599.00"},"fee_retained":"0.00","net_due_before":"5598.00","net_due_after":"3599.00","at":"2026-10-23T09:00+05:30","actor_type":"agent","actor_id":"sales-agent","trigger":"customer_request","confirmed_at":"2026-10-23T09:00+05:30"}]
}""")
RESERVATION = json.loads("""{
 "slot":{"date":"2026-10-25","start":"11:00","end":"11:30"},
 "items":[{"sku":"SKU-3001-40-NAV","quantity":1,"item_status":"prepared"}],
 "status_history":[{"status":"confirmed","sku":null,"at":"2026-10-22T19:20+05:30","actor_type":"agent","actor_id":"sales-agent","trigger":"customer_request"},{"status":"prepared","sku":"SKU-3001-40-NAV","at":"2026-10-25T10:30+05:30","actor_type":"staff","actor_id":"STF-011","trigger":"staff_update"}]
}""")
# not in DATA_SPEC's examples: a return, a feedback and a journey with a cart and an escalation
RETURN = {"return_id": "RET-1", "line_id": "L2", "request_type": "exchange", "exchange_sku": "SKU-2001-8-BRO",
          "price_difference": "0.00", "reason": "too small", "eligibility": "eligible", "ineligible_reason": None,
          "status": "approved", "fee_retained": "0.00", "net_due_before": "3599.00", "net_due_after": "3599.00",
          "at": "2026-10-28T10:00+05:30", "actor_type": "system", "actor_id": "post_purchase",
          "trigger": "rules_approval", "confirmed_at": "2026-10-28T10:00+05:30"}

SHAPES = {
    "delivery_slot": TypeAdapter(TimeSlot), "shipping_address": TypeAdapter(ShippingAddress),
    "lines": TypeAdapter(list[OrderLine]), "applied_offers": TypeAdapter(list[AppliedOffer]),
    "status_history": TypeAdapter(list[OrderStatusEntry]), "changes": TypeAdapter(list[Change]),
    "returns": TypeAdapter(list[ReturnEntry]), "feedback": TypeAdapter(Feedback),
}
RESERVATION_SHAPES = {
    "slot": TypeAdapter(TimeSlot), "items": TypeAdapter(list[ReservationItem]),
    "status_history": TypeAdapter(list[ReservationStatusEntry]),
}


def as_json(adapter, value):
    return adapter.dump_python(value, mode="json")


def test_money_is_stored_as_an_exact_string():
    line = OrderLine.model_validate(ORDER["lines"][0])
    assert line.unit_price == Decimal("1999.00")
    assert line.model_dump(mode="json")["unit_price"] == "1999.00"


def test_data_spec_examples_match_the_models():
    order = {name: SHAPES[name].validate_python(value) for name, value in ORDER.items()}
    assert order["changes"][0].totals_after.final_total == Decimal("3599.00")
    reservation = {name: RESERVATION_SHAPES[name].validate_python(v) for name, v in RESERVATION.items()}
    assert reservation["status_history"][1].actor_type == "staff"


@pytest.mark.parametrize("model, data, problem", [
    (OrderLine, {**ORDER["lines"][0], "colour": "white"}, "unknown key"),
    (OrderLine, {**ORDER["lines"][0], "line_status": "lost"}, "unknown status"),
    (OrderLine, {**ORDER["lines"][0], "unit_price": "-1.00"}, "negative money"),
    (OrderLine, {**ORDER["lines"][0], "unit_price": "19.999"}, "more than 2 decimals"),
    (OrderLine, {**ORDER["lines"][0], "quantity": 0}, "zero quantity"),
    (OrderStatusEntry, {**ORDER["status_history"][0], "at": "2026-10-22T19:30"}, "time without offset"),
    (OrderStatusEntry, {**ORDER["status_history"][0], "trigger": "because"}, "unknown trigger"),
    (Feedback, {"rating": 6}, "rating above 5"),
    (CartLine, {"sku": "SKU-1001-M-WHI"}, "missing quantity"),
])
def test_bad_shapes_are_refused(model, data, problem):
    with pytest.raises(ValidationError):
        model.model_validate(data)


@pytest.fixture
def connection(test_database):
    with test_database.connect() as connection:
        transaction = connection.begin()
        yield connection
        transaction.rollback()


@pytest.mark.integration
def test_order_json_round_trips_through_postgres(connection):
    stored = {name: as_json(SHAPES[name], SHAPES[name].validate_python(value)) for name, value in ORDER.items()}
    stored["returns"] = [RETURN]
    stored["feedback"] = {"rating": 5, "comment": None}
    connection.execute(insert(orders), {
        "order_id": "ORD-20001", "customer_id": "CUST-0002", "checkout_key": "CHK-0002-0001", "order_channel": "app",
        "fulfillment_method": "ship_to_home", "fulfillment_location_id": "LOC-W01", "subtotal": Decimal("4499.00"),
        "discount_total": Decimal("800.00"), "points_redeemed": 200, "points_value": Decimal("100.00"),
        "final_total": Decimal("3599.00"), "net_amount_due": Decimal("3599.00"), "order_status": "placed",
        "confirmed_at": "2026-10-22T19:29+05:30", **stored,
    })
    row = connection.execute(select(orders).where(orders.c.order_id == "ORD-20001")).mappings().one()
    for name, adapter in SHAPES.items():
        assert adapter.validate_python(row[name]) == adapter.validate_python(stored[name]), name


@pytest.mark.integration
def test_reservation_json_round_trips_through_postgres(connection):
    stored = {n: as_json(RESERVATION_SHAPES[n], RESERVATION_SHAPES[n].validate_python(v)) for n, v in RESERVATION.items()}
    connection.execute(insert(reservations), {
        "reservation_id": "RSV-801", "customer_id": "CUST-0004", "location_id": "LOC-S01",
        "reservation_status": "confirmed", "confirmed_at": "2026-10-22T19:20+05:30", **stored,
    })
    row = connection.execute(select(reservations).where(reservations.c.reservation_id == "RSV-801")).mappings().one()
    for name, adapter in RESERVATION_SHAPES.items():
        assert adapter.validate_python(row[name]) == adapter.validate_python(stored[name]), name


@pytest.mark.integration
def test_journey_json_round_trips_through_postgres(connection):
    cart = TypeAdapter(list[CartLine])
    escalation = Escalation(status="waiting", summary="Wants a wedding outfit; asked for a person.")
    stored_cart = as_json(cart, [CartLine(sku="SKU-3001-40-NAV", quantity=1)])
    connection.execute(journeys.update().where(journeys.c.customer_id == "CUST-0004").values(
        cart=stored_cart, shortlist=["PRD-3001"], declined_suggestions=["PRD-6001"],
        stated_preferences={"occasion": "wedding", "blazer_size": "40"},
        escalation=escalation.model_dump(mode="json"),
    ))
    row = connection.execute(select(journeys).where(journeys.c.customer_id == "CUST-0004")).mappings().one()
    assert cart.validate_python(row["cart"]) == cart.validate_python(stored_cart)
    assert Escalation.model_validate(row["escalation"]) == escalation
    assert row["stated_preferences"] == {"occasion": "wedding", "blazer_size": "40"}
