from datetime import datetime, timedelta

import pytest
from sqlalchemy import insert

from backend.db.schema import orders, payment_attempts, refunds, reservations, stock_holds
from backend.domain.clock import IST
from backend.domain.record_mismatches import MISMATCH_QUERIES, Mismatch, count_mismatches_by_kind, find_mismatches
from tests.sample_rows import hold_row, line, order_row, payment_row, refund_row, reservation_row

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=IST)
OLD = NOW - timedelta(minutes=10)    # past the grace period
RECENT = NOW - timedelta(minutes=1)  # still inside it
SKU, STORE = "SKU-2001-9-BRO", "LOC-W01"


def insert_rows(connection, table, *rows):
    connection.execute(insert(table), list(rows))


def test_a_clean_database_has_no_mismatches(rolled_back_connection):
    assert find_mismatches(rolled_back_connection, NOW) == []
    assert count_mismatches_by_kind([]) == dict.fromkeys(MISMATCH_QUERIES, 0)


def test_every_kind_of_mismatch_is_found_and_settled_records_are_not(rolled_back_connection):
    insert_rows(rolled_back_connection, orders,
        order_row("ORD-OK", net_amount_due="999.00", lines=[line("L1", SKU), line("L2", SKU, status="removed")]),
        order_row("ORD-CANCEL-NO-REFUND", status="cancelled", net_amount_due="0.00"),
        order_row("ORD-CANCEL-REFUNDED", status="cancelled", net_amount_due="0.00"),
        order_row("ORD-PLACED", checkout_key="CHK-ORDERED"))
    insert_rows(rolled_back_connection, payment_attempts,
        payment_row("PAY-OK", checkout_key="CHK-ORD-OK", amount="999.00", order_id="ORD-OK"),
        payment_row("PAY-NO-REFUND", checkout_key="CHK-ORD-CANCEL-NO-REFUND", amount="999.00",
                    order_id="ORD-CANCEL-NO-REFUND"),
        payment_row("PAY-REFUNDED", checkout_key="CHK-ORD-CANCEL-REFUNDED", amount="999.00",
                    order_id="ORD-CANCEL-REFUNDED"),
        payment_row("PAY-ORPHAN", checkout_key="CHK-ORPHAN", amount="500.00", at=OLD),
        payment_row("PAY-JUST-PAID", checkout_key="CHK-JUST-PAID", amount="500.00", at=RECENT),
        payment_row("PAY-STOCK-GONE", checkout_key="CHK-STOCK-GONE", amount="500.00", at=OLD))
    insert_rows(rolled_back_connection, refunds,
        refund_row("RFD-FAILED", amount="999.00", status="failed", order_id="ORD-CANCEL-REFUNDED"),
        refund_row("RFD-RETRIED", amount="999.00", status="completed", order_id="ORD-CANCEL-REFUNDED",
                   retry_of="RFD-FAILED"),
        refund_row("RFD-STUCK", amount="500.00", status="initiated", payment_id="PAY-STOCK-GONE",
                   source_type="stock_unavailable", source_ref="CHK-STOCK-GONE", at=OLD))
    insert_rows(rolled_back_connection, reservations,
        reservation_row("RSV-CANCELLED", status="cancelled",
                        items=[{"sku": SKU, "quantity": 1, "item_status": "pending"}]),
        reservation_row("RSV-MISSING", items=[{"sku": SKU, "quantity": 1, "item_status": "not_found"}]),
        reservation_row("RSV-OK", items=[{"sku": SKU, "quantity": 1, "item_status": "prepared"}]))
    hold = {"sku": SKU, "location_id": STORE}
    insert_rows(rolled_back_connection, stock_holds,
        hold_row("HLD-OK-ORDER", **hold, reference_id="ORD-OK", order_line_id="L1"),
        hold_row("HLD-OK-CHECKOUT", **hold, status="active", reference_type="checkout", reference_id="CHK-OPEN",
                 expires_at=NOW + timedelta(minutes=10)),
        hold_row("HLD-OK-RESERVATION", **hold, status="active", reference_type="reservation", reference_id="RSV-OK",
                 expires_at=NOW + timedelta(hours=1)),
        hold_row("HLD-OK-RELEASED", **hold, status="released", reference_id="ORD-CANCEL-NO-REFUND"),
        hold_row("HLD-EXPIRED", **hold, status="active", reference_type="checkout", reference_id="CHK-OLD",
                 expires_at=OLD),
        hold_row("HLD-REMOVED-LINE", **hold, reference_id="ORD-OK", order_line_id="L2"),
        hold_row("HLD-CANCELLED-ORDER", **hold, reference_id="ORD-CANCEL-NO-REFUND", order_line_id="L1"),
        hold_row("HLD-CHECKOUT-ORDERED", **hold, status="active", reference_type="checkout",
                 reference_id="CHK-ORDERED", expires_at=NOW + timedelta(minutes=10)),
        hold_row("HLD-CANCELLED-RESERVATION", **hold, status="active", reference_type="reservation",
                 reference_id="RSV-CANCELLED", expires_at=NOW + timedelta(hours=1)),
        hold_row("HLD-NOT-FOUND-ITEM", **hold, status="active", reference_type="reservation",
                 reference_id="RSV-MISSING", expires_at=NOW + timedelta(hours=1)))

    found = find_mismatches(rolled_back_connection, NOW)

    assert set(found) == {
        Mismatch("order_unbalanced", "ORD-CANCEL-NO-REFUND"),  # held 999.00 but entitled to 0
        Mismatch("payment_without_order", "CHK-ORPHAN"),  # CHK-JUST-PAID is in grace; CHK-STOCK-GONE has a refund
        Mismatch("refund_not_completed", "RFD-STUCK"),  # RFD-FAILED was retried and completed
        Mismatch("hold_not_released", "HLD-EXPIRED"),
        Mismatch("hold_not_released", "HLD-REMOVED-LINE"),
        Mismatch("hold_not_released", "HLD-CANCELLED-ORDER"),
        Mismatch("hold_not_released", "HLD-CHECKOUT-ORDERED"),
        Mismatch("hold_not_released", "HLD-CANCELLED-RESERVATION"),
        Mismatch("hold_not_released", "HLD-NOT-FOUND-ITEM"),
    }
    assert len(found) == len(set(found))
    assert count_mismatches_by_kind(found) == {
        "order_unbalanced": 1, "payment_without_order": 1, "refund_not_completed": 1, "hold_not_released": 6}


def test_a_failed_refund_that_was_not_retried_is_reported(rolled_back_connection):
    insert_rows(rolled_back_connection, orders, order_row("ORD-F", status="cancelled", net_amount_due="0.00"))
    insert_rows(rolled_back_connection, payment_attempts,
                payment_row("PAY-F", checkout_key="CHK-ORD-F", amount="999.00", order_id="ORD-F"))
    insert_rows(rolled_back_connection, refunds,
                refund_row("RFD-F", amount="999.00", status="failed", order_id="ORD-F"))
    assert find_mismatches(rolled_back_connection, NOW) == [Mismatch("refund_not_completed", "RFD-F")]


def test_refunds_promising_more_than_was_paid_are_reported(rolled_back_connection):
    insert_rows(rolled_back_connection, orders, order_row("ORD-OVER", status="cancelled", net_amount_due="0.00"))
    insert_rows(rolled_back_connection, payment_attempts,
        payment_row("PAY-OVER", checkout_key="CHK-ORD-OVER", amount="999.00", order_id="ORD-OVER"))
    insert_rows(rolled_back_connection, refunds,
        refund_row("RFD-A", amount="999.00", status="completed", order_id="ORD-OVER"),
        refund_row("RFD-B", amount="100.00", status="completed", order_id="ORD-OVER", source_type="return",
                   source_ref="RET-1"))
    assert find_mismatches(rolled_back_connection, NOW) == [Mismatch("order_unbalanced", "ORD-OVER")]
