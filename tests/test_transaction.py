from datetime import datetime

import pytest
from sqlalchemy import func, insert, select, text

from backend.db.schema import payment_attempts, stock_holds
from backend.domain.clock import IST, FixedClock
from backend.domain.transaction import WriteOutsideTransaction, open_transaction, write_operation
from tests.sample_rows import hold_row, payment_row

pytestmark = pytest.mark.integration

CLOCK = FixedClock(datetime(2026, 10, 24, 12, 0, tzinfo=IST))


@write_operation
def create_hold(worker_connection, sku, location_id):
    """Stands in for a worker's write operation, such as Inventory's create_hold."""
    hold_id = worker_connection.next_id("hold")
    worker_connection.execute(insert(stock_holds).values(hold_row(hold_id, sku=sku, location_id=location_id)))
    return hold_id


def hold_ids(engine):
    with engine.connect() as connection:
        return set(connection.execute(select(stock_holds.c.hold_id)).scalars())


def test_a_transaction_commits_when_its_block_ends(committing_database):
    with open_transaction(committing_database, CLOCK) as transaction:
        hold_id = transaction.run_write_operation(create_hold, "SKU-2001-9-BRO", "LOC-W01")
    assert hold_ids(committing_database) == {hold_id}


def test_an_exception_rolls_the_whole_transaction_back(committing_database):
    with pytest.raises(RuntimeError), open_transaction(committing_database, CLOCK) as transaction:
        transaction.run_write_operation(create_hold, "SKU-2001-9-BRO", "LOC-W01")
        transaction.run_write_operation(create_hold, "SKU-3001-40-NAV", "LOC-S01")
        raise RuntimeError("a later step failed")
    assert hold_ids(committing_database) == set()


def test_a_failing_commit_check_rolls_the_transaction_back(committing_database):
    def refuse(connection):
        assert connection.execute(select(func.count()).select_from(stock_holds)).scalar_one() == 1  # sees the write
        raise ValueError("does not add up")

    with pytest.raises(ValueError), open_transaction(committing_database, CLOCK) as transaction:
        transaction.run_write_operation(create_hold, "SKU-2001-9-BRO", "LOC-W01")
        transaction.add_commit_check(refuse)
    assert hold_ids(committing_database) == set()


def test_a_payment_attempt_stays_recorded_when_the_order_transaction_rolls_back(committing_database):
    # A 2.3: the attempt is its own transaction, committed before order creation starts
    with pytest.raises(RuntimeError), open_transaction(committing_database, CLOCK) as order_transaction:
        order_transaction.run_write_operation(create_hold, "SKU-2001-9-BRO", "LOC-W01")
        with open_transaction(committing_database, CLOCK) as payment_transaction:
            payment_transaction.connection.execute(insert(payment_attempts).values(
                payment_row("PAY-T1", checkout_key="CHK-T1", amount="999.00", outcome="declined")))
        raise RuntimeError("order creation failed")
    with committing_database.connect() as connection:
        outcomes = connection.execute(select(payment_attempts.c.payment_id, payment_attempts.c.outcome)).all()
    assert outcomes == [("PAY-T1", "declined")]
    assert hold_ids(committing_database) == set()


def test_the_transaction_carries_its_clock(test_database):
    with open_transaction(test_database, CLOCK) as transaction:
        assert transaction.clock.now() == CLOCK.now()


def test_a_write_operation_cannot_be_called_directly():
    with pytest.raises(WriteOutsideTransaction):
        create_hold(None, "SKU-2001-9-BRO", "LOC-W01")


def test_only_write_operations_run_inside_a_transaction(test_database):
    def plain_function(worker_connection):
        return worker_connection

    with pytest.raises(TypeError), open_transaction(test_database, CLOCK) as transaction:
        transaction.run_write_operation(plain_function)


@pytest.mark.parametrize("statement", ["COMMIT", "ROLLBACK", "BEGIN", "SELECT 1"])
def test_a_write_operation_cannot_run_raw_sql(committing_database, statement):
    @write_operation
    def sneaky(worker_connection):
        worker_connection.execute(text(statement))

    with pytest.raises(TypeError), open_transaction(committing_database, CLOCK) as transaction:
        transaction.run_write_operation(create_hold, "SKU-2001-9-BRO", "LOC-W01")
        transaction.run_write_operation(sneaky)
    assert hold_ids(committing_database) == set()


def test_a_worker_connection_is_dead_once_its_transaction_ends(test_database):
    @write_operation
    def keep_worker_connection(worker_connection):
        return worker_connection

    with open_transaction(test_database, CLOCK) as transaction:
        worker_connection = transaction.run_write_operation(keep_worker_connection)
    with pytest.raises(WriteOutsideTransaction):
        worker_connection.execute(select(stock_holds.c.hold_id))
    with pytest.raises(WriteOutsideTransaction):
        create_hold(worker_connection, "SKU-2001-9-BRO", "LOC-W01")
