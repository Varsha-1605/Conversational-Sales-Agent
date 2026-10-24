from datetime import datetime, timedelta, timezone

import pytest

from backend.domain.audit_fields import Actor, audit_fields, confirmed_audit_fields
from backend.domain.clock import IST, FixedClock
from backend.domain.models import Cancellation, OrderStatusEntry, ReservationStatusEntry

CLOCK = FixedClock(datetime(2026, 10, 24, 12, 0, tzinfo=IST))


@pytest.mark.parametrize("actor, expected", [
    (Actor.customer("CUST-0002"), ("customer", "CUST-0002")),
    (Actor.sales_agent(), ("agent", "sales-agent")),
    (Actor.staff("STF-011"), ("staff", "STF-011")),
    (Actor.system("expiry-job"), ("system", "expiry-job")),
])
def test_actors(actor, expected):
    assert (actor.type, actor.id) == expected


def test_status_entries_carry_the_audit_fields():
    entry = OrderStatusEntry(status="placed", **audit_fields(Actor.sales_agent(), "customer_request", CLOCK))
    assert entry.model_dump(mode="json") == {
        "status": "placed", "at": "2026-10-24T12:00:00+05:30", "actor_type": "agent", "actor_id": "sales-agent",
        "trigger": "customer_request",
    }
    staff_entry = ReservationStatusEntry(status="prepared", sku="SKU-3001-40-NAV",
                                         **audit_fields(Actor.staff("STF-011"), "staff_update", CLOCK))
    assert staff_entry.actor_type == "staff"


def test_confirmed_entries_carry_the_confirmation_in_ist():
    confirmed_at = datetime(2026, 10, 24, 6, 29, tzinfo=timezone.utc)
    cancellation = Cancellation(
        status="completed", reason="No longer needed", fee_retained="0.00", net_due_before="3999.00",
        net_due_after="0.00", **confirmed_audit_fields(Actor.customer("CUST-0009"), "customer_request", CLOCK,
                                                       confirmed_at))
    assert cancellation.confirmed_at == confirmed_at
    assert cancellation.confirmed_at.utcoffset() == timedelta(hours=5, minutes=30)


def test_a_confirmation_without_a_timezone_is_refused():
    with pytest.raises(ValueError):
        confirmed_audit_fields(Actor.customer("CUST-0009"), "customer_request", CLOCK, datetime(2026, 10, 24, 12, 0))


def test_an_unknown_trigger_is_refused_by_the_entry_model():
    with pytest.raises(ValueError):
        OrderStatusEntry(status="placed", **audit_fields(Actor.sales_agent(), "because", CLOCK))
