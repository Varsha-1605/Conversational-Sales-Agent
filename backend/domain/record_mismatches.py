from collections import Counter
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import text

# DB-48, PRD NF-10: find records that disagree with each other, so they can be resolved and their counts shown on
# Integration status. Run by the expiry job or on demand; it only reads.
#
# A payment, refund or hold younger than SETTLEMENT_GRACE is left out: the step that settles it (order creation, the
# refund call, the 60-second expiry job) may simply not have run yet.
SETTLEMENT_GRACE = timedelta(minutes=5)

# an order whose captured payments minus refund chains is more than it may keep (H > E), or whose refund
# obligations exceed what was paid (R > P): a refund is missing or over-promised
_UNBALANCED_ORDERS = text("""
    WITH paid AS (
        SELECT order_id, SUM(amount) AS p FROM payment_attempts
        WHERE outcome = 'success' AND order_id IS NOT NULL GROUP BY order_id),
    owed AS (
        SELECT order_id, SUM(amount) AS r FROM refunds
        WHERE retry_of IS NULL AND order_id IS NOT NULL GROUP BY order_id)
    SELECT o.order_id FROM orders o
    LEFT JOIN paid ON paid.order_id = o.order_id
    LEFT JOIN owed ON owed.order_id = o.order_id
    WHERE COALESCE(paid.p, 0) - COALESCE(owed.r, 0) > o.net_amount_due
       OR COALESCE(owed.r, 0) > COALESCE(paid.p, 0)
    ORDER BY o.order_id
""")

# a captured checkout payment with no order and no stock_unavailable refund covering it
_PAYMENTS_WITHOUT_ORDER = text("""
    SELECT p.checkout_key FROM payment_attempts p
    WHERE p.outcome = 'success' AND p.purpose = 'checkout' AND p.order_id IS NULL AND p.captured_at < :settled_before
      AND NOT EXISTS (SELECT 1 FROM orders o WHERE o.checkout_key = p.checkout_key)
      AND p.amount > COALESCE((
          SELECT SUM(r.amount) FROM refunds r
          WHERE r.retry_of IS NULL AND r.order_id IS NULL AND r.source_type = 'stock_unavailable'
            AND r.source_ref = p.checkout_key), 0)
    ORDER BY p.checkout_key
""")

# refund chains whose latest record failed (owed, not retried) or is still initiated after the grace period
_REFUNDS_NOT_COMPLETED = text("""
    WITH RECURSIVE chain AS (
        SELECT refund_id AS root_id, refund_id, refund_status, created_at FROM refunds WHERE retry_of IS NULL
        UNION ALL
        SELECT chain.root_id, r.refund_id, r.refund_status, r.created_at
        FROM refunds r JOIN chain ON r.retry_of = chain.refund_id)
    SELECT root_id FROM chain
    WHERE NOT EXISTS (SELECT 1 FROM refunds r WHERE r.retry_of = chain.refund_id)
      AND (refund_status = 'failed' OR (refund_status = 'initiated' AND created_at < :settled_before))
    ORDER BY root_id
""")

# holds still reducing availability although what they belong to no longer needs them (DATA_SPEC 4.5, 4.6)
_HOLDS_NOT_RELEASED = text("""
    SELECT h.hold_id FROM stock_holds h
    WHERE h.hold_status IN ('active', 'committed') AND (
        (h.hold_status = 'active' AND h.expires_at < :settled_before)
        OR (h.reference_type = 'order' AND EXISTS (
            SELECT 1 FROM orders o WHERE o.order_id = h.reference_id AND (
                o.order_status = 'cancelled'
                OR EXISTS (SELECT 1 FROM jsonb_array_elements(o.lines) line
                           WHERE line->>'line_id' = h.order_line_id AND line->>'line_status' = 'removed'))))
        OR (h.reference_type = 'checkout' AND EXISTS (
            SELECT 1 FROM orders o WHERE o.checkout_key = h.reference_id))
        OR (h.reference_type = 'reservation' AND EXISTS (
            SELECT 1 FROM reservations v WHERE v.reservation_id = h.reference_id AND (
                v.reservation_status IN ('completed', 'lapsed', 'cancelled')
                OR EXISTS (SELECT 1 FROM jsonb_array_elements(v.items) item
                           WHERE item->>'sku' = h.sku AND item->>'item_status' = 'not_found'))))
    )
    ORDER BY h.hold_id
""")

MISMATCH_QUERIES = {
    "order_unbalanced": _UNBALANCED_ORDERS,
    "payment_without_order": _PAYMENTS_WITHOUT_ORDER,
    "refund_not_completed": _REFUNDS_NOT_COMPLETED,
    "hold_not_released": _HOLDS_NOT_RELEASED,
}


@dataclass(frozen=True)
class Mismatch:
    kind: str       # a key of MISMATCH_QUERIES
    reference: str  # order_id, checkout_key, the refund chain's first refund_id, or hold_id


def find_mismatches(connection, now) -> list[Mismatch]:
    """Every record that disagrees with another, as of `now`."""
    settled_before = now - SETTLEMENT_GRACE
    return [
        Mismatch(kind, reference)
        for kind, query in MISMATCH_QUERIES.items()
        for reference in connection.execute(query, {"settled_before": settled_before}).scalars()
    ]


def count_mismatches_by_kind(mismatches) -> dict[str, int]:
    """Unresolved counts for Integration status; every kind is present, with 0 when clean."""
    counts = Counter(mismatch.kind for mismatch in mismatches)
    return {kind: counts[kind] for kind in MISMATCH_QUERIES}
