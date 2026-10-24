from dataclasses import dataclass

from backend.domain.clock import to_ist
from backend.domain.models.common import ActorType, Trigger

# OP-02, DATA_SPEC 4: there is no separate audit log. Each entry carries who did it, what caused it, when, and the
# customer's confirmation where SA-08 needs one. These helpers build those fields the same way everywhere, from the
# transaction's clock, so they can be passed straight into the typed entry models (OrderStatusEntry, Change, ...).

SALES_AGENT_ID = "sales-agent"


@dataclass(frozen=True)
class Actor:
    """Who performed an action: a customer, the Sales Agent, a staff member or a rules-based service."""

    type: ActorType
    id: str

    @classmethod
    def customer(cls, customer_id):
        return cls("customer", customer_id)

    @classmethod
    def sales_agent(cls):
        return cls("agent", SALES_AGENT_ID)

    @classmethod
    def staff(cls, staff_id):
        return cls("staff", staff_id)

    @classmethod
    def system(cls, service):
        return cls("system", service)


def audit_fields(actor: Actor, trigger: Trigger, clock) -> dict:
    """at, actor_type, actor_id and trigger for a status entry, stamped now in IST."""
    return {"at": clock.now(), "actor_type": actor.type, "actor_id": actor.id, "trigger": trigger}


def confirmed_audit_fields(actor: Actor, trigger: Trigger, clock, confirmed_at) -> dict:
    """The audit fields plus the customer's confirmation, for changes, cancellations and returns (SA-08).
    Raises ValueError for a confirmation time without a timezone."""
    return {**audit_fields(actor, trigger, clock), "confirmed_at": to_ist(confirmed_at)}
