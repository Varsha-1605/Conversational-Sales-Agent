from datetime import datetime, timedelta

from agents.workers.inventory.contract import HoldReferenceType, HoldStatus, StockState

# Prototype values from data/raw/business_rules.json (stale_stock, stock_holds) and ARCHITECTURE 12 (low-stock
# threshold). The client's own rules (Q-07) replace these values, or this module, without touching callers.
# tests/test_rules_match_business_rules.py fails if a value here drifts from the file.
STALE_AFTER = timedelta(days=30)        # stale_stock.threshold_days; older stock is "not confirmed" (DR-08)
LOW_STOCK_AT_OR_BELOW = 2               # the client gave no value; ARCHITECTURE 12 suggests "2 or fewer"
CHECKOUT_HOLD = timedelta(minutes=15)   # stock_holds.checkout_minutes
CLICK_COLLECT_READY = timedelta(days=3)  # stock_holds.click_collect_ready_days; read by the expiry job (Phase 11)

# a hold for stock that is sold starts committed; one waiting for a payment or a visit starts active
_STARTS_COMMITTED = frozenset({"order", "exchange"})


def is_stale(last_updated_at: datetime, now: datetime) -> bool:
    return now - last_updated_at > STALE_AFTER


def stock_state(available_quantity: int, last_updated_at: datetime, now: datetime) -> StockState:
    """The state shown for a stock record, from the quantity left after holds."""
    if is_stale(last_updated_at, now):
        return "unconfirmed"
    if available_quantity <= 0:
        return "out_of_stock"
    if available_quantity <= LOW_STOCK_AT_OR_BELOW:
        return "low_stock"
    return "in_stock"


def starting_hold_status(reference_type: HoldReferenceType) -> HoldStatus:
    return "committed" if reference_type in _STARTS_COMMITTED else "active"


def hold_expires_at(reference_type: HoldReferenceType, slot_end: datetime | None, now: datetime) -> datetime | None:
    """When a new hold lapses: 15 minutes for a checkout, the end of the slot for a reservation, never for sold
    stock. Raises ValueError for a reservation whose slot has already ended."""
    if reference_type == "checkout":
        return now + CHECKOUT_HOLD
    if reference_type == "reservation":
        if slot_end is None or slot_end <= now:
            raise ValueError("a reservation hold must last until a slot end that is still in the future")
        return slot_end
    return None
