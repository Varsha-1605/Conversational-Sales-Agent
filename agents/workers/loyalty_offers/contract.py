from typing import Literal

from pydantic import AwareDatetime, Field

from backend.domain.models.common import Model, Money
from backend.domain.models.order import AppliedOffer

# AGENTS 3.3, ARCHITECTURE 5.4, API 3.7: what the Loyalty and Offers worker accepts and returns.

CouponRejection = Literal[
    "unknown_code", "not_yet_valid", "expired", "already_used", "not_first_order", "minimum_not_met",
    "nothing_left_to_discount",  # earlier offers already took the whole amount
]
PointsRejection = Literal["below_minimum", "more_than_balance", "above_share_limit"]


class QuoteLineRequest(Model):
    sku: str
    quantity: int = Field(ge=1)


class QuoteRequest(Model):
    lines: list[QuoteLineRequest] = Field(min_length=1)
    coupon_code: str | None = None
    points_to_use: int = Field(default=0, ge=0)
    # where the order will be fulfilled; with it, a line whose stock there is not confirmed is rejected
    fulfillment_location_id: str | None = None


class QuoteLine(Model):
    sku: str
    product_id: str
    product_name: str
    quantity: int
    unit_price: Money  # the list price
    line_total: Money


class RejectedLine(Model):
    sku: str
    reason: Literal["stock_not_confirmed"]


class CouponOutcome(Model):
    code: str
    is_applied: bool
    rejection_reason: CouponRejection | None = None


class PointsOutcome(Model):
    requested: int
    applied: int
    max_redeemable: int  # most the customer may use on this order: balance, share limit and minimum considered
    rejection_reason: PointsRejection | None = None


class QuotePricing(Model):
    """The money side of a quote, in the order the stacking rule applies it."""

    applied_offers: list[AppliedOffer]
    coupon: CouponOutcome | None
    points: PointsOutcome
    subtotal: Money
    discount_total: Money
    points_value: Money
    final_total: Money
    net_amount_due: Money  # equals final_total at checkout (DATA_SPEC 4.2)
    savings: Money  # discounts plus the value of the points used


class QuoteReply(QuotePricing):
    lines: list[QuoteLine]
    rejected_lines: list[RejectedLine]  # left out of every amount above
    quoted_at: AwareDatetime
