from typing import Literal

from pydantic import Field

from backend.domain.models.common import Audit, Model

ReservationStatus = Literal["confirmed", "customer_arrived", "completed", "lapsed", "cancelled"]
ItemStatus = Literal["pending", "prepared", "not_found"]


class ReservationItem(Model):
    """reservations.items[] (DATA_SPEC 4.6)."""

    sku: str
    quantity: int = Field(ge=1)
    item_status: ItemStatus


class ReservationStatusEntry(Audit):
    """reservations.status_history[]: a reservation status, or an item marked prepared or not_found."""

    status: ReservationStatus | Literal["prepared", "not_found"]
    sku: str | None = None  # set for item updates


# reservations.slot is a common.TimeSlot.
