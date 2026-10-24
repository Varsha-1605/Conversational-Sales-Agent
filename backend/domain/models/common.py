from datetime import date, time
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

# Shapes of the JSON fields stored in PostgreSQL (DATA_SPEC section 4, DB-28). Every stored value goes through
# one of these models, so the shape cannot drift. Money is an exact decimal, written as a string ("1999.00").

Money = Annotated[Decimal, Field(max_digits=12, decimal_places=2, ge=0)]
SignedMoney = Annotated[Decimal, Field(max_digits=12, decimal_places=2)]  # only the exchange price difference

ActorType = Literal["customer", "agent", "staff", "system"]
Trigger = Literal[
    "customer_request", "staff_update", "payment_result", "scheduled_expiry",
    "data_import", "logistics_update", "rules_approval",
]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")  # an unknown key is an error, not silently stored


class Audit(Model):
    """Audit fields stored on the entry they describe (DATA_SPEC 4, OP-02)."""

    at: AwareDatetime
    actor_type: ActorType
    actor_id: str
    trigger: Trigger


class ConfirmedAudit(Audit):
    confirmed_at: AwareDatetime | None = None  # the customer's confirmation, where SA-08 requires one


class TimeSlot(Model):
    """A delivery window or a try-on slot."""

    date: date
    start: time
    end: time
