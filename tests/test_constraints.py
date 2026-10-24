from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import insert
from sqlalchemy.exc import IntegrityError

from backend.db.schema import orders, payment_attempts, refunds
from backend.domain.clock import IST

pytestmark = pytest.mark.integration

AT = datetime(2026, 10, 24, 12, 0, tzinfo=IST)


@pytest.fixture
def connection(test_database):
    # every test runs in one transaction that is rolled back, so nothing it inserts is kept
    with test_database.connect() as connection:
        transaction = connection.begin()
        yield connection
        transaction.rollback()


def payment(payment_id, attempt_number, outcome="success", checkout_key="CHK-T1"):
    success = outcome == "success"
    return {
        "payment_id": payment_id, "checkout_key": checkout_key, "purpose": "checkout", "customer_id": "CUST-0002",
        "method_type": "upi", "amount": Decimal("999.00"), "outcome": outcome,
        "failed_stage": None if success else "authorization",
        "authorized_at": AT if success else None, "captured_at": AT if success else None,
        "actor_type": "customer", "actor_id": "CUST-0002", "attempt_number": attempt_number, "created_at": AT,
    }


def refund(refund_id, retry_of=None, status="failed", order_id=None, source_type="stock_unavailable", source_ref="CHK-T1"):
    return {
        "refund_id": refund_id, "order_id": order_id, "payment_id": "PAY-T1", "source_type": source_type,
        "source_ref": source_ref, "amount": Decimal("999.00"), "refund_status": status, "retry_of": retry_of,
        "created_at": AT,
    }


def refused(connection, table, row):
    # True when the database rejects the row; a savepoint keeps the rest of the test's transaction usable
    savepoint = connection.begin_nested()
    try:
        connection.execute(insert(table), row)
    except IntegrityError:
        savepoint.rollback()
        return True
    savepoint.commit()
    return False


def test_second_success_for_one_checkout_key_is_refused(connection):
    connection.execute(insert(payment_attempts), payment("PAY-T1", 1))
    assert refused(connection, payment_attempts, payment("PAY-T2", 2))


def test_failed_attempts_and_a_success_can_share_a_checkout_key(connection):
    connection.execute(insert(payment_attempts), payment("PAY-T1", 1, outcome="declined"))
    connection.execute(insert(payment_attempts), payment("PAY-T2", 2, outcome="declined"))
    assert not refused(connection, payment_attempts, payment("PAY-T3", 3))


def test_second_refund_chain_for_one_checkout_is_refused(connection):
    # no order exists (stock_unavailable), so order_id is empty: the index must still treat both as the same cause
    connection.execute(insert(payment_attempts), payment("PAY-T1", 1))
    connection.execute(insert(refunds), refund("RFD-T1"))
    assert refused(connection, refunds, refund("RFD-T2"))


def test_second_refund_chain_for_one_order_cause_is_refused(connection):
    connection.execute(insert(payment_attempts), payment("PAY-T1", 1))
    connection.execute(insert(orders), {
        "order_id": "ORD-T1", "customer_id": "CUST-0002", "checkout_key": "CHK-T1", "order_channel": "web",
        "fulfillment_method": "ship_to_home", "fulfillment_location_id": "LOC-W01", "lines": [],
        "subtotal": Decimal("999.00"), "final_total": Decimal("999.00"), "net_amount_due": Decimal("0.00"),
        "order_status": "cancelled", "status_history": [], "confirmed_at": AT,
    })
    cause = {"order_id": "ORD-T1", "source_type": "cancellation", "source_ref": "ORD-T1"}
    connection.execute(insert(refunds), refund("RFD-T1", **cause))
    assert refused(connection, refunds, refund("RFD-T2", **cause))


def test_retry_of_a_failed_refund_stays_in_its_chain(connection):
    connection.execute(insert(payment_attempts), payment("PAY-T1", 1))
    connection.execute(insert(refunds), refund("RFD-T1"))
    assert not refused(connection, refunds, refund("RFD-T2", retry_of="RFD-T1", status="completed"))
    assert refused(connection, refunds, refund("RFD-T3", retry_of="RFD-T1"))  # each failed record is retried once
