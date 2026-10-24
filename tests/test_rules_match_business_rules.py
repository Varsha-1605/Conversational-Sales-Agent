import json
from datetime import date, timedelta
from decimal import Decimal

from agents.workers.inventory import stock_rules
from agents.workers.loyalty_offers import pricing_rules
from config import PROJECT_ROOT

# The workers hold the prototype values as constants (ARCHITECTURE 7.1). This fails when a constant and
# data/raw/business_rules.json disagree, so the file stays the reference for them (P-04).
BUSINESS_RULES = json.loads((PROJECT_ROOT / "data" / "raw" / "business_rules.json").read_text(encoding="utf-8"))


def discount_of(entry):
    return pricing_rules.Discount(entry["type"], Decimal(entry["value"]))


def scope_of(condition):
    return pricing_rules.LineScope(
        frozenset(condition.get("category_in", ())), frozenset(condition.get("subcategory_in", ())))


def test_stock_rules_match_the_file():
    assert stock_rules.STALE_AFTER == timedelta(days=BUSINESS_RULES["stale_stock"]["threshold_days"])
    assert stock_rules.CHECKOUT_HOLD == timedelta(minutes=BUSINESS_RULES["stock_holds"]["checkout_minutes"])
    ready_days = BUSINESS_RULES["stock_holds"]["click_collect_ready_days"]
    assert stock_rules.CLICK_COLLECT_READY == timedelta(days=ready_days)


def test_loyalty_rules_match_the_file():
    loyalty = BUSINESS_RULES["loyalty"]
    assert pricing_rules.POINT_VALUE == Decimal(loyalty["point_value_inr"])
    assert pricing_rules.MIN_POINTS_PER_ORDER == loyalty["min_points_per_order"]
    assert pricing_rules.MAX_SHARE_OF_ORDER_VALUE == Decimal(loyalty["max_share_of_order_value"])


def test_promotions_match_the_file():
    expected = [
        pricing_rules.Promotion(
            entry["rule_id"], entry["description"], discount_of(entry["discount"]),
            date.fromisoformat(entry["valid_from"]), date.fromisoformat(entry["valid_to"]),
            scope_of(entry["condition"]),
            Decimal(entry["condition"]["min_cart_inr"]) if "min_cart_inr" in entry["condition"] else None)
        for entry in BUSINESS_RULES["promotions"]
    ]
    assert list(pricing_rules.PROMOTIONS) == expected


def test_coupons_match_the_file():
    expected = [
        pricing_rules.Coupon(
            entry["code"], entry["description"], discount_of(entry["discount"]),
            date.fromisoformat(entry["valid_from"]), date.fromisoformat(entry["valid_to"]),
            Decimal(entry["condition"]["min_cart_inr"]), entry["condition"].get("first_order_only", False),
            entry["usage_limit_per_customer"])
        for entry in BUSINESS_RULES["coupons"]
    ]
    assert list(pricing_rules.COUPONS) == expected


def test_personalized_offers_match_the_file():
    expected = [
        pricing_rules.PersonalizedOffer(
            entry["rule_id"], entry["description"], entry["condition"]["loyalty_tier"],
            discount_of(entry["discount"]), date.fromisoformat(entry["valid_from"]),
            date.fromisoformat(entry["valid_to"]), scope_of(entry["condition"]))
        for entry in BUSINESS_RULES["personalized_offers"]
    ]
    assert list(pricing_rules.PERSONALIZED_OFFERS) == expected


def test_the_pricing_code_still_fits_the_stacking_rule_in_the_file():
    # price_cart is written for exactly this order and one coupon, and gives personalized offers only with consent
    stacking = BUSINESS_RULES["stacking"]
    assert stacking["order"] == [
        "best single automatic promotion", "one coupon", "personalized offer", "loyalty points"]
    assert stacking["max_coupons_per_order"] == 1
    assert stacking["total_cannot_go_below_zero"] is True
    assert all(offer["requires_personalization_allowed"] for offer in BUSINESS_RULES["personalized_offers"])
