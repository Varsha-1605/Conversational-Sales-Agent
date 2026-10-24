from typing import Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from backend.domain.models.common import Model

# AGENTS 3.2, ARCHITECTURE 5.4: what the Inventory worker accepts and returns.

StockState = Literal["in_stock", "low_stock", "out_of_stock", "unconfirmed"]  # unconfirmed = stock data is stale
FulfillmentMethod = Literal["ship_to_home", "click_collect", "in_store"]
HoldReferenceType = Literal["checkout", "reservation", "order", "exchange"]
HoldStatus = Literal["active", "committed", "released", "lapsed"]


class AvailabilityRequest(Model):
    sku: str
    location_id: str


class AvailabilityReply(Model):
    sku: str
    location_id: str
    state: StockState
    available_quantity: int | None  # None when unconfirmed: a stale record never gives a quantity
    stock_updated_at: AwareDatetime | None  # None when the client has no stock record for this location


class FulfillmentOptionsRequest(Model):
    sku: str
    quantity: int = Field(default=1, ge=1)


class FulfillmentOption(Model):
    method: FulfillmentMethod
    location_id: str
    location_name: str
    state: StockState
    available_quantity: int | None
    is_available: bool  # confirmed stock covers the requested quantity


class FulfillmentOptionsReply(Model):
    sku: str
    options: list[FulfillmentOption]


class HoldLine(Model):
    sku: str
    location_id: str
    quantity: int = Field(ge=1)
    order_line_id: str | None = None  # the order line sold (order holds) or the line exchanged (exchange holds)


class CreateHoldRequest(Model):
    reference_type: HoldReferenceType
    reference_id: str  # checkout_key, reservation_id, order_id, or return_id for an exchange
    lines: list[HoldLine] = Field(min_length=1)
    expires_at: AwareDatetime | None = None  # a reservation hold only: the end of the booked slot

    @model_validator(mode="after")
    def _only_reservation_holds_take_an_expiry(self) -> Self:
        if (self.reference_type == "reservation") != (self.expires_at is not None):
            raise ValueError("expires_at is required for a reservation hold and not allowed for any other")
        return self


class HoldReply(Model):
    hold_id: str
    sku: str
    location_id: str
    quantity: int
    reference_type: HoldReferenceType
    reference_id: str
    order_line_id: str | None
    hold_status: HoldStatus
    expires_at: AwareDatetime | None


class CreateHoldReply(Model):
    holds: list[HoldReply]


class CommitHoldRequest(Model):
    hold_id: str
    order_id: str
    order_line_id: str


class ReleaseHoldRequest(Model):
    hold_id: str
