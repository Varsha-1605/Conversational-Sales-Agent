from datetime import datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import insert, update

from agents.workers.loyalty_offers import cart_quote
from agents.workers.loyalty_offers.contract import QuoteRequest
from backend.db.schema import orders
from backend.domain.clock import IST
from tests.sample_rows import order_row

pytestmark = pytest.mark.integration

ORDER_TIME = datetime(2026, 10, 22, 19, 30, tzinfo=IST)  # when the worked examples of DATA_SPEC 4.2 were placed
LATER = datetime(2026, 10, 25, 12, 0, tzinfo=IST)

SHIRT, SHOES, KURTA = "SKU-1001-M-WHI", "SKU-2001-9-BRO", "SKU-4001-M-IVO"
STALE_KURTA = "SKU-4001-L-IVO"  # its LOC-S03 stock record is the stale one

WELCOME300_USED = [{"rule_id": "WELCOME300", "rule_description": "Rs 300 off a first order of Rs 1,500 or more",
                    "coupon_code": "WELCOME300", "discount_amount": "300.00"}]


def request_for(*skus, **fields):
    return QuoteRequest(lines=[{"sku": sku, "quantity": 1} for sku in skus], **fields)


def quote(connection, sql_server, customer_id, request, at=ORDER_TIME):
    with sql_server.connect() as client:
        return cart_quote.quote(connection, client, customer_id, request, at)


def offers_of(reply):
    return {offer.rule_id: offer.discount_amount for offer in reply.applied_offers}


def place_order(connection, order_id, customer_id, **fields):
    connection.execute(insert(orders).values(order_row(order_id, customer_id=customer_id, **fields)))


# the worked examples, priced from the real catalogue

def test_order_ord_20001_is_priced_exactly_and_again_after_the_shirt_is_removed(rolled_back_connection, sql_server):
    reply = quote(rolled_back_connection, sql_server, "CUST-0002",
                  request_for(SHIRT, SHOES, coupon_code="WELCOME300", points_to_use=200))
    assert [(line.sku, line.unit_price) for line in reply.lines] == [
        (SHIRT, Decimal("1999.00")), (SHOES, Decimal("4499.00"))]
    assert offers_of(reply) == {"PROMO-FEST-500": Decimal("500.00"), "WELCOME300": Decimal("300.00")}
    assert (reply.subtotal, reply.discount_total, reply.points_value, reply.final_total) == (
        Decimal("6498.00"), Decimal("800.00"), Decimal("100.00"), Decimal("5598.00"))
    assert (reply.net_amount_due, reply.savings, reply.quoted_at) == (Decimal("5598.00"), Decimal("900.00"), ORDER_TIME)

    after_removal = quote(rolled_back_connection, sql_server, "CUST-0002",
                          request_for(SHOES, coupon_code="WELCOME300", points_to_use=200))
    assert after_removal.final_total == Decimal("3599.00")


def test_order_ord_20002_for_a_customer_who_declined_personalization(rolled_back_connection, sql_server):
    reply = quote(rolled_back_connection, sql_server, "CUST-0009", request_for(SHIRT, SHOES))  # Gold tier
    assert offers_of(reply) == {"PROMO-FEST-500": Decimal("500.00")}
    assert reply.final_total == Decimal("5998.00")
    assert quote(rolled_back_connection, sql_server, "CUST-0009", request_for(SHOES)).final_total == Decimal("3999.00")


# consent and tier

def test_a_gold_customer_who_allows_personalization_gets_the_tier_offer(rolled_back_connection, sql_server):
    reply = quote(rolled_back_connection, sql_server, "CUST-0001", request_for(SHOES), LATER)
    assert offers_of(reply) == {"PROMO-FEST-500": Decimal("500.00"), "PERS-GOLD-FW15": Decimal("674.85")}
    assert reply.final_total == Decimal("3324.15")


def test_a_gold_customer_who_declined_personalization_gets_no_personalized_or_tier_offer(
        rolled_back_connection, sql_server):
    reply = quote(rolled_back_connection, sql_server, "CUST-0009", request_for(SHOES), LATER)
    assert "PERS-GOLD-FW15" not in offers_of(reply)


def test_a_customer_who_declined_personalization_can_still_use_a_coupon_and_points(rolled_back_connection, sql_server):
    reply = quote(rolled_back_connection, sql_server, "CUST-0009",
                  request_for(SHOES, coupon_code="WELCOME300", points_to_use=100), LATER)
    assert offers_of(reply) == {"PROMO-FEST-500": Decimal("500.00"), "WELCOME300": Decimal("300.00")}
    assert reply.points.applied == 100


def test_the_seasonal_promotion_applies_to_ethnic_wear(rolled_back_connection, sql_server):
    reply = quote(rolled_back_connection, sql_server, "CUST-0002", request_for(KURTA), LATER)
    assert offers_of(reply) == {"PROMO-ETHNIC-10": Decimal("219.90")}
    assert reply.final_total == Decimal("1979.10")


# the first-order coupon and coupon use ignore cancelled orders

def test_a_coupon_used_on_an_order_is_freed_when_that_order_is_cancelled(rolled_back_connection, sql_server):
    place_order(rolled_back_connection, "ORD-T9001", "CUST-0003", applied_offers=WELCOME300_USED)
    request = request_for(SHOES, coupon_code="WELCOME300")

    used = quote(rolled_back_connection, sql_server, "CUST-0003", request, LATER)
    assert (used.coupon.is_applied, used.coupon.rejection_reason) == (False, "already_used")
    assert "WELCOME300" not in offers_of(used)

    rolled_back_connection.execute(
        update(orders).where(orders.c.order_id == "ORD-T9001").values(order_status="cancelled"))
    freed = quote(rolled_back_connection, sql_server, "CUST-0003", request, LATER)
    assert freed.coupon.is_applied and offers_of(freed)["WELCOME300"] == Decimal("300.00")


def test_any_earlier_order_ends_the_first_order_coupon_until_it_is_cancelled(rolled_back_connection, sql_server):
    place_order(rolled_back_connection, "ORD-T9002", "CUST-0007")
    request = request_for(SHOES, coupon_code="welcome300")  # the code is not case sensitive

    refused = quote(rolled_back_connection, sql_server, "CUST-0007", request, LATER)
    assert refused.coupon.rejection_reason == "not_first_order"

    rolled_back_connection.execute(
        update(orders).where(orders.c.order_id == "ORD-T9002").values(order_status="cancelled"))
    assert quote(rolled_back_connection, sql_server, "CUST-0007", request, LATER).coupon.is_applied


def test_an_expired_and_an_unknown_coupon_come_back_with_their_reason(rolled_back_connection, sql_server):
    expired = quote(rolled_back_connection, sql_server, "CUST-0002", request_for(SHOES, coupon_code="SUMMER200"), LATER)
    unknown = quote(rolled_back_connection, sql_server, "CUST-0002", request_for(SHOES, coupon_code="NOPE"), LATER)
    assert (expired.coupon.rejection_reason, unknown.coupon.rejection_reason) == ("expired", "unknown_code")
    assert expired.final_total == unknown.final_total == Decimal("3999.00")


# points

def test_points_already_spent_on_orders_are_not_available_again(rolled_back_connection, sql_server):
    place_order(rolled_back_connection, "ORD-T9003", "CUST-0002", points_redeemed=850)  # 900 seeded, 50 left
    refused = quote(rolled_back_connection, sql_server, "CUST-0002", request_for(SHOES, points_to_use=100), LATER)
    assert (refused.points.applied, refused.points.rejection_reason) == (0, "more_than_balance")
    assert refused.points.max_redeemable == 0  # 50 points are under the 100-point minimum


def test_points_come_off_after_the_offers(rolled_back_connection, sql_server):
    reply = quote(rolled_back_connection, sql_server, "CUST-0002", request_for(SHOES, points_to_use=200), LATER)
    assert (reply.discount_total, reply.points_value, reply.final_total) == (
        Decimal("500.00"), Decimal("100.00"), Decimal("3899.00"))


# stale stock

def test_a_line_with_stale_stock_at_the_chosen_location_is_rejected_and_left_out_of_every_amount(
        rolled_back_connection, sql_server, stock_data_now):
    reply = quote(rolled_back_connection, sql_server, "CUST-0002",
                  request_for(STALE_KURTA, SHIRT, fulfillment_location_id="LOC-S03"), stock_data_now)
    assert [(line.sku, line.reason) for line in reply.rejected_lines] == [(STALE_KURTA, "stock_not_confirmed")]
    assert [line.sku for line in reply.lines] == [SHIRT]
    assert reply.subtotal == Decimal("1999.00")


def test_a_quote_where_every_line_is_stale_prices_nothing(rolled_back_connection, sql_server, stock_data_now):
    reply = quote(rolled_back_connection, sql_server, "CUST-0002",
                  request_for(STALE_KURTA, fulfillment_location_id="LOC-S03"), stock_data_now)
    assert (reply.lines, reply.subtotal, reply.final_total) == ([], Decimal("0.00"), Decimal("0.00"))


@pytest.mark.parametrize("location_id", ["LOC-W01", None])
def test_the_same_item_is_quoted_where_its_stock_is_current_or_no_place_is_chosen(
        rolled_back_connection, sql_server, stock_data_now, location_id):
    reply = quote(rolled_back_connection, sql_server, "CUST-0002",
                  request_for(STALE_KURTA, fulfillment_location_id=location_id), stock_data_now)
    assert reply.rejected_lines == [] and [line.sku for line in reply.lines] == [STALE_KURTA]


def test_a_blank_coupon_code_means_no_coupon(rolled_back_connection, sql_server):
    reply = quote(rolled_back_connection, sql_server, "CUST-0002", request_for(SHOES, coupon_code="   "), LATER)
    assert reply.coupon is None


def test_the_same_sku_twice_is_priced_as_two_lines(rolled_back_connection, sql_server):
    reply = quote(rolled_back_connection, sql_server, "CUST-0002", request_for(SHIRT, SHIRT), LATER)
    assert [line.sku for line in reply.lines] == [SHIRT, SHIRT]
    assert reply.subtotal == Decimal("3998.00")


def test_an_unknown_fulfilment_location_rejects_no_line(rolled_back_connection, sql_server, stock_data_now):
    reply = quote(rolled_back_connection, sql_server, "CUST-0002",
                  request_for(SHIRT, fulfillment_location_id="LOC-NOWHERE"), stock_data_now)
    assert reply.rejected_lines == [] and [line.sku for line in reply.lines] == [SHIRT]


# bad requests

def test_an_unknown_customer_or_sku_cannot_be_quoted(rolled_back_connection, sql_server):
    with pytest.raises(LookupError):
        quote(rolled_back_connection, sql_server, "CUST-9999", request_for(SHOES))
    with pytest.raises(cart_quote.UnknownSku, match="SKU-NOPE"):
        quote(rolled_back_connection, sql_server, "CUST-0002", request_for(SHOES, "SKU-NOPE"))


@pytest.mark.parametrize("fields", [
    {"lines": []},
    {"lines": [{"sku": SHOES, "quantity": 0}]},
    {"lines": [{"sku": SHOES, "quantity": 1, "price": "1.00"}]},  # a caller cannot name a price
    {"lines": [{"sku": SHOES, "quantity": 1}], "points_to_use": -1},
    {"lines": [{"sku": SHOES, "quantity": 1}], "customer_id": "CUST-0001"},  # nor the customer
])
def test_a_malformed_quote_request_is_refused(fields):
    with pytest.raises(ValidationError):
        QuoteRequest(**fields)
