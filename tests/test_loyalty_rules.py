from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from agents.workers.loyalty_offers import pricing_rules
from agents.workers.loyalty_offers.pricing_rules import (
    CustomerFacts, Discount, PricedLine, Promotion, check_coupon, price_cart,
)

DAY = date(2026, 10, 25)  # inside every prototype promotion, coupon and personalized offer window


def item(price, category="Men's Clothing", subcategory="Casual Shirts", quantity=1, sku="SKU-TEST"):
    return PricedLine(sku=sku, product_id="PRD-TEST", product_name="Test item", category=category,
                      subcategory=subcategory, quantity=quantity, unit_price=Decimal(price))


SHIRT = item("1999.00", sku="SKU-1001-M-WHI")
SHOES = item("4499.00", "Men's Footwear", "Formal Shoes", sku="SKU-2001-9-BRO")
KURTA = item("2199.00", "Men's Ethnic Wear", "Kurtas", sku="SKU-4001-M-IVO")
JACKET = item("3499.00", "Women's Clothing", "Jackets", sku="SKU-5002-S-BLU")

SILVER = CustomerFacts(loyalty_tier="Silver", personalization_allowed=True, points_balance=900,
                       has_prior_order=False, times_coupon_used=0)
GOLD = replace(SILVER, loyalty_tier="Gold")


def amounts(pricing):
    return {offer.rule_id: offer.discount_amount for offer in pricing.applied_offers}


# the worked examples of DATA_SPEC 4.2 and 4.4.1

def test_order_ord_20001_at_checkout_and_after_the_shirt_is_removed():
    day = date(2026, 10, 22)
    checkout = price_cart([SHIRT, SHOES], SILVER, day, "WELCOME300", points_requested=200)
    assert amounts(checkout) == {"PROMO-FEST-500": Decimal("500.00"), "WELCOME300": Decimal("300.00")}
    assert (checkout.subtotal, checkout.discount_total, checkout.points_value, checkout.final_total) == (
        Decimal("6498.00"), Decimal("800.00"), Decimal("100.00"), Decimal("5598.00"))
    assert checkout.savings == Decimal("900.00")

    after_removal = price_cart([SHOES], SILVER, day, "WELCOME300", points_requested=200)
    assert (after_removal.subtotal, after_removal.final_total) == (Decimal("4499.00"), Decimal("3599.00"))


def test_order_ord_20002_has_only_the_festive_offer_and_no_points():
    day = date(2026, 10, 22)
    declined = replace(GOLD, personalization_allowed=False, points_balance=1800)
    checkout = price_cart([SHIRT, SHOES], declined, day)
    assert amounts(checkout) == {"PROMO-FEST-500": Decimal("500.00")}
    assert checkout.final_total == Decimal("5998.00")
    assert price_cart([SHOES], declined, day).final_total == Decimal("3999.00")


def test_the_coupon_is_dropped_when_the_cart_falls_below_its_minimum():
    pricing = price_cart([item("1400.00")], SILVER, DAY, "WELCOME300")
    assert pricing.coupon.rejection_reason == "minimum_not_met"
    assert pricing.final_total == Decimal("1400.00")


# automatic promotions: the best single one applies

@pytest.mark.parametrize("lines, day, expected", [
    ([item("2999.99")], DAY, {}),                                                # one paisa under the festive minimum
    ([item("3000.00")], DAY, {"PROMO-FEST-500": "500.00"}),                      # exactly the minimum
    ([item("3000.00")], date(2026, 11, 15), {"PROMO-FEST-500": "500.00"}),       # last day is included
    ([item("3000.00")], date(2026, 11, 16), {}),
    ([item("3000.00")], date(2026, 10, 14), {}),
    ([KURTA], DAY, {"PROMO-ETHNIC-10": "219.90"}),                               # ethnic wear only, cart under 3000
    ([KURTA], date(2026, 10, 19), {}),
    ([KURTA], date(2026, 11, 10), {"PROMO-ETHNIC-10": "219.90"}),
    ([KURTA], date(2026, 11, 11), {}),
    ([KURTA, SHIRT], DAY, {"PROMO-FEST-500": "500.00"}),                         # 500 beats 10% of 2199
    ([KURTA] * 3, DAY, {"PROMO-ETHNIC-10": "659.70"}),                           # 10% of 6597 beats 500
    ([JACKET], DAY, {"PROMO-FEST-500": "500.00"}),                               # the monsoon offer has expired
    ([item("1999.00", "Men's Ethnic Wear", "Kurtas"), item("500.00")], DAY, {"PROMO-ETHNIC-10": "199.90"}),
])
def test_only_the_best_valid_promotion_applies(lines, day, expected):
    assert amounts(price_cart(lines, SILVER, day)) == {rule: Decimal(amount) for rule, amount in expected.items()}


# coupons: valid, invalid and expired, each with its reason

@pytest.mark.parametrize("code, subtotal, day, customer, reason", [
    ("WELCOME300", "1500.00", DAY, SILVER, None),
    ("  welcome300 ", "1500.00", DAY, SILVER, None),
    ("WELCOME300", "1499.99", DAY, SILVER, "minimum_not_met"),
    ("WELCOME300", "5000.00", DAY, replace(SILVER, has_prior_order=True), "not_first_order"),
    ("WELCOME300", "5000.00", DAY, replace(SILVER, has_prior_order=True, times_coupon_used=1), "already_used"),
    ("WELCOME300", "5000.00", date(2026, 12, 31), SILVER, None),
    ("WELCOME300", "5000.00", date(2027, 1, 1), SILVER, "expired"),
    ("WELCOME300", "5000.00", date(2025, 12, 31), SILVER, "not_yet_valid"),
    ("SUMMER200", "1500.00", DAY, SILVER, "expired"),
    ("SUMMER200", "1500.00", date(2026, 5, 1), SILVER, None),
    ("SUMMER200", "1500.00", date(2026, 3, 31), SILVER, "not_yet_valid"),
    ("NOSUCHCODE", "1500.00", DAY, SILVER, "unknown_code"),
])
def test_a_coupon_is_accepted_or_refused_with_its_reason(code, subtotal, day, customer, reason):
    check = check_coupon(code, Decimal(subtotal), day, customer)
    assert check.rejection_reason == reason
    assert (check.coupon is not None) == (reason is None)


def test_a_refused_coupon_takes_nothing_off():
    pricing = price_cart([SHIRT, SHOES], SILVER, DAY, "SUMMER200")
    assert pricing.coupon.is_applied is False
    assert "SUMMER200" not in amounts(pricing)


# personalized and tier offers need the customer's consent (LO-03, P-12)

def test_a_gold_customer_who_allows_personalization_gets_fifteen_percent_off_footwear():
    pricing = price_cart([SHOES, SHIRT], GOLD, DAY)
    assert amounts(pricing)["PERS-GOLD-FW15"] == Decimal("674.85")  # 15% of the shoes only


@pytest.mark.parametrize("customer, day", [
    (replace(GOLD, personalization_allowed=False), DAY),   # declined personalization
    (SILVER, DAY),                                         # not the Gold tier
    (GOLD, date(2026, 10, 19)),                            # before the offer starts
    (GOLD, date(2026, 12, 1)),                             # after it ends
])
def test_no_personalized_offer_without_consent_tier_or_dates(customer, day):
    assert "PERS-GOLD-FW15" not in amounts(price_cart([SHOES], customer, day))


def test_a_customer_who_declined_personalization_still_gets_promotions_coupons_and_points():
    declined = replace(GOLD, personalization_allowed=False)
    pricing = price_cart([SHOES, SHIRT], declined, DAY, "WELCOME300", points_requested=100)
    assert amounts(pricing) == {"PROMO-FEST-500": Decimal("500.00"), "WELCOME300": Decimal("300.00")}
    assert pricing.points.applied == 100


# stacking: promotion, coupon, personalized offer, then points

def test_offers_stack_in_the_documented_order():
    pricing = price_cart([SHOES, SHIRT], GOLD, DAY, "WELCOME300", points_requested=200)
    assert [offer.rule_id for offer in pricing.applied_offers] == ["PROMO-FEST-500", "WELCOME300", "PERS-GOLD-FW15"]
    assert pricing.discount_total == Decimal("1474.85")
    assert pricing.final_total == Decimal("6498.00") - Decimal("1474.85") - Decimal("100.00")
    assert pricing.net_amount_due == pricing.final_total
    assert pricing.savings == Decimal("1574.85")


def test_the_coupon_is_the_only_one_and_carries_its_code():
    pricing = price_cart([SHIRT, SHOES], SILVER, DAY, "WELCOME300")
    coupon_offers = [offer for offer in pricing.applied_offers if offer.coupon_code]
    assert [(offer.rule_id, offer.coupon_code) for offer in coupon_offers] == [("WELCOME300", "WELCOME300")]


def test_the_total_never_goes_below_zero(monkeypatch):
    nearly_everything = Promotion(
        "PROMO-TEST", "Test", Discount("amount", Decimal("1800.00")), date(2026, 1, 1), date(2026, 12, 31))
    monkeypatch.setattr(pricing_rules, "PROMOTIONS", (nearly_everything,))
    pricing = price_cart([item("1999.00")], GOLD, DAY, "WELCOME300", points_requested=100)
    assert amounts(pricing) == {"PROMO-TEST": Decimal("1800.00"), "WELCOME300": Decimal("199.00")}  # 199 left, not 300
    assert pricing.final_total == Decimal("0.00")
    assert pricing.points.applied == 0


def test_a_coupon_is_not_marked_applied_when_earlier_offers_left_nothing_to_take_off(monkeypatch):
    everything = Promotion(
        "PROMO-TEST", "Test", Discount("amount", Decimal("1999.00")), date(2026, 1, 1), date(2026, 12, 31))
    monkeypatch.setattr(pricing_rules, "PROMOTIONS", (everything,))
    pricing = price_cart([item("1999.00")], SILVER, DAY, "WELCOME300")
    assert (pricing.coupon.is_applied, pricing.coupon.rejection_reason) == (False, "nothing_left_to_discount")
    assert amounts(pricing) == {"PROMO-TEST": Decimal("1999.00")}


# loyalty points

@pytest.mark.parametrize("requested, balance, amount, applied, reason", [
    (0, 900, "5698.00", 0, None),
    (99, 900, "5698.00", 0, "below_minimum"),
    (100, 900, "5698.00", 100, None),
    (200, 150, "5698.00", 0, "more_than_balance"),
    (400, 900, "1000.00", 400, None),                  # 20% of 1000 is 200 rupees = 400 points
    (401, 900, "1000.00", 0, "above_share_limit"),
])
def test_points_are_applied_or_refused_with_their_reason(requested, balance, amount, applied, reason):
    outcome = pricing_rules.check_points(requested, balance, Decimal(amount))
    assert (outcome.applied, outcome.rejection_reason) == (applied, reason)


@pytest.mark.parametrize("balance, amount, most", [
    (900, "5698.00", 900),    # the balance is the limit
    (5000, "1000.00", 400),   # the 20% share is the limit
    (50, "5698.00", 0),       # under the 100-point minimum: nothing can be used
    (900, "150.00", 0),       # 20% of 150 is 60 points, under the minimum
])
def test_the_most_points_a_customer_may_use(balance, amount, most):
    assert pricing_rules.check_points(0, balance, Decimal(amount)).max_redeemable == most


def test_a_point_is_worth_fifty_paise():
    pricing = price_cart([SHIRT, SHOES], SILVER, DAY, points_requested=101)
    assert pricing.points_value == Decimal("50.50")
