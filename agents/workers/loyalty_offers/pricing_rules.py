from dataclasses import dataclass
from datetime import date
from decimal import ROUND_FLOOR, Decimal
from typing import Literal

from agents.workers.loyalty_offers.contract import (
    CouponOutcome, CouponRejection, PointsOutcome, PointsRejection, QuotePricing,
)
from backend.domain.models.order import AppliedOffer
from backend.domain.money import to_money

# Prototype values from data/raw/business_rules.json (loyalty, promotions, coupons, personalized_offers, stacking).
# The client's own rules (LO-07, Q-07) replace these values, or this module, without touching the code that uses them.
# tests/test_rules_match_business_rules.py fails if a value here drifts from the file.
#
# Stacking order (LO-05): the best single automatic promotion, then one coupon, then one personalized offer, then
# loyalty points. Every offer is worked out on the lines' list prices; the total never goes below zero.

ZERO = Decimal("0.00")

POINT_VALUE = Decimal("0.50")           # loyalty.point_value_inr
MIN_POINTS_PER_ORDER = 100              # loyalty.min_points_per_order
MAX_SHARE_OF_ORDER_VALUE = Decimal("0.20")  # loyalty.max_share_of_order_value


@dataclass(frozen=True)
class PricedLine:
    sku: str
    product_id: str
    product_name: str
    category: str
    subcategory: str
    quantity: int
    unit_price: Decimal

    @property
    def line_total(self) -> Decimal:
        return to_money(self.unit_price * self.quantity)


@dataclass(frozen=True)
class Discount:
    kind: Literal["amount", "percent"]
    value: Decimal


@dataclass(frozen=True)
class LineScope:
    """The lines an offer applies to. With no category or subcategory it applies to every line."""

    categories: frozenset[str] = frozenset()
    subcategories: frozenset[str] = frozenset()

    def matches(self, line: PricedLine) -> bool:
        if not (self.categories or self.subcategories):
            return True
        return line.category in self.categories or line.subcategory in self.subcategories


@dataclass(frozen=True)
class Promotion:
    rule_id: str
    description: str
    discount: Discount
    valid_from: date
    valid_to: date
    scope: LineScope = LineScope()
    minimum_cart: Decimal | None = None


@dataclass(frozen=True)
class Coupon:
    code: str
    description: str
    discount: Discount
    valid_from: date
    valid_to: date
    minimum_cart: Decimal | None = None
    first_order_only: bool = False  # "first order" = no earlier order placed through this app
    usage_limit_per_customer: int = 1


@dataclass(frozen=True)
class PersonalizedOffer:
    rule_id: str
    description: str
    loyalty_tier: str
    discount: Discount
    valid_from: date
    valid_to: date
    scope: LineScope = LineScope()


PROMOTIONS = (
    Promotion("PROMO-FEST-500", "Festive offer: Rs 500 off carts of Rs 3,000 or more",
              Discount("amount", Decimal("500.00")), date(2026, 10, 15), date(2026, 11, 15),
              minimum_cart=Decimal("3000.00")),
    Promotion("PROMO-ETHNIC-10", "10% off ethnic wear (time-limited)",
              Discount("percent", Decimal("10")), date(2026, 10, 20), date(2026, 11, 10),
              scope=LineScope(categories=frozenset({"Men's Ethnic Wear", "Women's Ethnic Wear"}))),
    Promotion("PROMO-MONSOON-15", "Monsoon sale: 15% off jackets (expired)",
              Discount("percent", Decimal("15")), date(2026, 7, 1), date(2026, 9, 30),
              scope=LineScope(subcategories=frozenset({"Jackets"}))),
)

COUPONS = (
    Coupon("WELCOME300", "Rs 300 off a first order of Rs 1,500 or more", Discount("amount", Decimal("300.00")),
           date(2026, 1, 1), date(2026, 12, 31), minimum_cart=Decimal("1500.00"), first_order_only=True),
    Coupon("SUMMER200", "Rs 200 off (expired)", Discount("amount", Decimal("200.00")),
           date(2026, 4, 1), date(2026, 6, 30), minimum_cart=Decimal("1000.00")),
)

PERSONALIZED_OFFERS = (
    PersonalizedOffer("PERS-GOLD-FW15", "Gold tier: 15% off footwear", "Gold",
                      Discount("percent", Decimal("15")), date(2026, 10, 20), date(2026, 11, 30),
                      scope=LineScope(categories=frozenset({"Men's Footwear", "Footwear"}))),
)


@dataclass(frozen=True)
class CustomerFacts:
    """What pricing needs to know about the customer. cart_quote.py reads these; the rules never touch a database."""

    loyalty_tier: str
    personalization_allowed: bool
    points_balance: int
    has_prior_order: bool  # an order that is not cancelled
    times_coupon_used: int  # uses of the coupon in this quote on orders that are not cancelled; 0 with no coupon


def _subtotal(lines) -> Decimal:
    return to_money(sum((line.line_total for line in lines), ZERO))


def _discount_amount(discount: Discount, scope: LineScope, lines) -> Decimal:
    base = sum((line.line_total for line in lines if scope.matches(line)), ZERO)
    if discount.kind == "amount":
        return min(discount.value, base)
    return to_money(base * discount.value / 100)


def _is_valid_on(offer, day) -> bool:
    return offer.valid_from <= day <= offer.valid_to


def best_promotion(lines, day: date) -> tuple[Promotion, Decimal] | None:
    """The automatic promotion that saves the most (the first listed wins a tie), or None."""
    subtotal = _subtotal(lines)
    candidates = []
    for promotion in PROMOTIONS:
        if not _is_valid_on(promotion, day) or (promotion.minimum_cart and subtotal < promotion.minimum_cart):
            continue
        amount = _discount_amount(promotion.discount, promotion.scope, lines)
        if amount > ZERO:
            candidates.append((promotion, amount))
    return max(candidates, key=lambda candidate: candidate[1], default=None)


@dataclass(frozen=True)
class CouponCheck:
    coupon: Coupon | None  # set when the coupon may be used
    rejection_reason: CouponRejection | None


def check_coupon(code: str, subtotal: Decimal, day: date, customer: CustomerFacts) -> CouponCheck:
    """Whether the customer may use this coupon now. The reasons are checked in a fixed order: unknown, dates, uses
    already made, first order, minimum cart (LO-02)."""
    coupon = next((candidate for candidate in COUPONS if candidate.code == code.strip().upper()), None)
    if coupon is None:
        return CouponCheck(None, "unknown_code")
    if day < coupon.valid_from:
        return CouponCheck(None, "not_yet_valid")
    if day > coupon.valid_to:
        return CouponCheck(None, "expired")
    if customer.times_coupon_used >= coupon.usage_limit_per_customer:
        return CouponCheck(None, "already_used")
    if coupon.first_order_only and customer.has_prior_order:
        return CouponCheck(None, "not_first_order")
    if coupon.minimum_cart and subtotal < coupon.minimum_cart:
        return CouponCheck(None, "minimum_not_met")
    return CouponCheck(coupon, None)


def best_personalized_offer(lines, customer: CustomerFacts, day: date) -> tuple[PersonalizedOffer, Decimal] | None:
    """The personalized offer that saves the most, or None. Only a customer who allowed personalization gets one."""
    if not customer.personalization_allowed:
        return None
    candidates = []
    for offer in PERSONALIZED_OFFERS:
        if offer.loyalty_tier != customer.loyalty_tier or not _is_valid_on(offer, day):
            continue
        amount = _discount_amount(offer.discount, offer.scope, lines)
        if amount > ZERO:
            candidates.append((offer, amount))
    return max(candidates, key=lambda candidate: candidate[1], default=None)


def check_points(requested: int, balance: int, amount_before_points: Decimal) -> PointsOutcome:
    """How many points the customer may use. A request outside the limits is refused with a reason, not trimmed,
    so the customer is never charged a price they did not ask for (LO-06)."""
    most_by_share = int((amount_before_points * MAX_SHARE_OF_ORDER_VALUE / POINT_VALUE)
                        .to_integral_value(rounding=ROUND_FLOOR))
    max_redeemable = min(balance, most_by_share)
    if max_redeemable < MIN_POINTS_PER_ORDER:
        max_redeemable = 0
    reason: PointsRejection | None = None
    if 0 < requested < MIN_POINTS_PER_ORDER:
        reason = "below_minimum"
    elif requested > balance:
        reason = "more_than_balance"
    elif requested > most_by_share:
        reason = "above_share_limit"
    return PointsOutcome(requested=requested, applied=0 if reason else requested, max_redeemable=max_redeemable,
                         rejection_reason=reason)


class _Ledger:
    """The running amount while offers are taken off, in stacking order."""

    def __init__(self, subtotal: Decimal):
        self.subtotal = subtotal
        self.remaining = subtotal
        self.offers: list[AppliedOffer] = []

    def take_off(self, rule_id, description, amount, coupon_code=None) -> Decimal:
        """Take an offer off, at most what is left. Returns the amount taken, which is zero when nothing is left."""
        amount = min(amount, self.remaining)  # the total never goes below zero
        if amount <= ZERO:
            return ZERO
        self.remaining -= amount
        self.offers.append(AppliedOffer(
            rule_id=rule_id, rule_description=description, coupon_code=coupon_code, discount_amount=amount))
        return amount


def _apply_coupon(ledger: _Ledger, code: str, lines, day: date, customer: CustomerFacts) -> CouponOutcome:
    check = check_coupon(code, ledger.subtotal, day, customer)
    outcome = CouponOutcome(code=code.strip().upper(), is_applied=False, rejection_reason=check.rejection_reason)
    if check.coupon is None:
        return outcome
    taken = ledger.take_off(check.coupon.code, check.coupon.description,
                            _discount_amount(check.coupon.discount, LineScope(), lines), check.coupon.code)
    if taken == ZERO:
        return outcome.model_copy(update={"rejection_reason": "nothing_left_to_discount"})
    return outcome.model_copy(update={"is_applied": True})


def price_cart(lines, customer: CustomerFacts, day: date, coupon_code: str | None = None,
               points_requested: int = 0) -> QuotePricing:
    """Price the lines for one customer on one day: offers in stacking order, then points."""
    subtotal = _subtotal(lines)
    ledger = _Ledger(subtotal)

    promotion = best_promotion(lines, day)
    if promotion:
        ledger.take_off(promotion[0].rule_id, promotion[0].description, promotion[1])

    coupon_outcome = _apply_coupon(ledger, coupon_code, lines, day, customer) if coupon_code else None

    personalized = best_personalized_offer(lines, customer, day)
    if personalized:
        ledger.take_off(personalized[0].rule_id, personalized[0].description, personalized[1])

    points = check_points(points_requested, customer.points_balance, ledger.remaining)
    points_value = to_money(points.applied * POINT_VALUE)
    discount_total = to_money(sum((offer.discount_amount for offer in ledger.offers), ZERO))
    final_total = ledger.remaining - points_value
    return QuotePricing(
        applied_offers=ledger.offers, coupon=coupon_outcome, points=points, subtotal=subtotal,
        discount_total=discount_total, points_value=points_value, final_total=final_total,
        net_amount_due=final_total, savings=discount_total + points_value)
