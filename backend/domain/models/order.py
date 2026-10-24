from typing import Literal

from pydantic import Field

from backend.domain.models.common import Audit, ConfirmedAudit, Model, Money, SignedMoney

OrderStatus = Literal["placed", "ready_for_pickup", "item_not_found", "shipped", "delivered", "collected", "cancelled"]


class ShippingAddress(Model):
    """orders.shipping_address (ship to home only)."""

    line1: str
    line2: str | None = None
    city: str
    pincode: str


class OrderLine(Model):
    """orders.lines[]."""

    line_id: str
    sku: str
    quantity: int = Field(ge=1)
    unit_price: Money
    line_status: Literal["active", "removed", "returned", "exchanged"]


class AppliedOffer(Model):
    """orders.applied_offers[]."""

    rule_id: str
    rule_description: str
    coupon_code: str | None = None
    discount_amount: Money


class OrderStatusEntry(Audit):
    """orders.status_history[]."""

    status: OrderStatus


class Totals(Model):
    """totals_before and totals_after on orders.changes[]."""

    subtotal: Money
    discount_total: Money
    points_value: Money
    final_total: Money


class NetDue(Model):
    """How an event changed what the business is entitled to keep (E); not a refund amount (DATA_SPEC 4.2)."""

    fee_retained: Money
    net_due_before: Money
    net_due_after: Money


class Change(NetDue, ConfirmedAudit):
    """orders.changes[]: a modification or a re-fulfil."""

    change_id: str
    type: Literal["remove_item", "reduce_quantity", "re_fulfil"]
    line_id: str | None = None  # empty for re_fulfil
    status: Literal["applied", "refused"]
    reason: str | None = None
    totals_before: Totals
    totals_after: Totals


class Cancellation(NetDue, ConfirmedAudit):
    """orders.cancellation."""

    status: Literal["completed", "refused"]
    reason: str | None = None


class ReturnEntry(NetDue, ConfirmedAudit):
    """orders.returns[]: a return or an exchange of one line."""

    return_id: str
    line_id: str
    request_type: Literal["return", "exchange"]
    exchange_sku: str | None = None
    price_difference: SignedMoney | None = None  # exchange only: replacement list price − original unit price
    reason: str
    eligibility: Literal["eligible", "not_eligible"]
    ineligible_reason: str | None = None
    status: Literal["requested", "refused", "approved", "completed"]


class Feedback(Model):
    """orders.feedback."""

    rating: int = Field(ge=1, le=5)
    comment: str | None = None


# orders.delivery_slot is a common.TimeSlot.
