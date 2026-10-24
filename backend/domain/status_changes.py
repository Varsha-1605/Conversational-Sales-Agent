from dataclasses import dataclass, field
from datetime import datetime

# DR-05: every status follows a defined set of allowed changes (DATA_SPEC 4.2 to 4.6, ARCHITECTURE 5.6, 7.4).
# The domain core checks a change here before it writes it; anything not listed is refused.


class StatusChangeNotAllowed(ValueError):
    """A status change the rules do not allow. Nothing has been written."""

    def __init__(self, record, current, new):
        self.record = record
        self.current = current
        self.new = new
        super().__init__(f"{record}: {current or 'new'} -> {new} is not allowed")


@dataclass(frozen=True, eq=False)
class StatusRules:
    record: str  # what the status belongs to, for error messages
    starting_statuses: frozenset[str]
    allowed_changes: dict[str, frozenset[str]] = field(default_factory=dict)

    @property
    def statuses(self) -> frozenset[str]:
        return self.starting_statuses.union(self.allowed_changes, *self.allowed_changes.values())

    def is_allowed_change(self, current, new) -> bool:
        return new in self.allowed_changes.get(current, ())

    def require_starting_status(self, status):
        """Raises StatusChangeNotAllowed unless a record may be created with this status."""
        if status not in self.starting_statuses:
            raise StatusChangeNotAllowed(self.record, None, status)

    def require_allowed_change(self, current, new):
        """Raises StatusChangeNotAllowed unless current -> new is an allowed change."""
        if not self.is_allowed_change(current, new):
            raise StatusChangeNotAllowed(self.record, current, new)


def _build_status_rules(record, starting_statuses, allowed_changes):
    return StatusRules(record, frozenset(starting_statuses),
                       {status: frozenset(changes) for status, changes in allowed_changes.items()})


# orders.order_status, per fulfillment method (DATA_SPEC 4.2, business_rules order_statuses and cancellation).
# item_not_found -> placed is the re-fulfil; the change is checked against the method the order had before it.
ORDER_STATUS_BY_METHOD = {
    "ship_to_home": _build_status_rules("order (ship to home)", {"placed"}, {
        "placed": {"shipped", "cancelled"},
        "shipped": {"delivered"},
    }),
    "click_collect": _build_status_rules("order (click & collect)", {"placed"}, {
        "placed": {"ready_for_pickup", "item_not_found", "cancelled"},
        "ready_for_pickup": {"collected", "cancelled"},
        "item_not_found": {"placed", "cancelled"},
    }),
    # a POS order is created as collected (A 7.5)
    "in_store": _build_status_rules("order (in store)", {"collected"}, {}),
}

# orders.returns[].status, for returns and exchanges alike
RETURN_OR_EXCHANGE_STATUS = _build_status_rules("return or exchange", {"requested"}, {
    "requested": {"approved", "refused"},
    "approved": {"completed"},
})

RESERVATION_STATUS = _build_status_rules("reservation", {"confirmed"}, {
    "confirmed": {"customer_arrived", "lapsed", "cancelled"},
    "customer_arrived": {"completed"},
})

# reservations.items[].item_status
RESERVATION_ITEM_STATUS = _build_status_rules("reservation item", {"pending"}, {
    "pending": {"prepared", "not_found"},
})

# stock_holds.hold_status: lapsed and released are final (DATA_SPEC 4.5)
STOCK_HOLD_STATUS = _build_status_rules("stock hold", {"active", "committed"}, {
    "active": {"committed", "released", "lapsed"},
    "committed": {"released"},
})

# payment_attempts.outcome: recorded once; resolving a timeout is the only allowed change (DATA_SPEC 4.3, PY-05).
# Which outcome fits which failed stage is enforced by the table's CHECK constraint.
PAYMENT_ATTEMPT_STATUS = _build_status_rules("payment attempt", {"success", "declined", "failed", "timeout"}, {
    "timeout": {"success", "declined", "failed"},
})

# refunds.refund_status: final once completed or failed; a failed record is retried as a new record (DATA_SPEC 4.4)
REFUND_STATUS = _build_status_rules("refund", {"initiated"}, {
    "initiated": {"completed", "failed"},
})

# journeys.escalation.status (ARCHITECTURE 5.6, SA-11)
ESCALATION_STATUS = _build_status_rules("escalation", {"waiting"}, {
    "waiting": {"with_associate"},
    "with_associate": {"resolved"},
})


def order_status_rules(fulfillment_method) -> StatusRules:
    """The order status rules for a fulfillment method. Raises KeyError for an unknown method."""
    return ORDER_STATUS_BY_METHOD[fulfillment_method]


def require_hold_can_commit(expires_at: datetime | None, now: datetime):
    """An active hold may become committed only before it expires; a lapsed hold never does (DATA_SPEC 4.5).
    Raises StatusChangeNotAllowed once expires_at has passed."""
    if expires_at is not None and now >= expires_at:
        raise StatusChangeNotAllowed(STOCK_HOLD_STATUS.record, "active (expired)", "committed")
