from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from decimal import Decimal
import random
from threading import Barrier

import pytest
from sqlalchemy import insert, select, update

from backend.db.schema import orders, payment_attempts, refunds
from backend.domain.clock import IST, FixedClock
from backend.domain.payment_balance import (
    BalanceScope, PaymentBalance, PaymentBalanceError, RefundChain, create_refund_if_due, group_refund_chains,
    load_payment_balance, require_balanced_payments, require_within_refund_cap,
)
from backend.domain.transaction import open_transaction
from tests.sample_rows import order_row, payment_row, refund_row

# DATA_SPEC 4.2 and 4.4, DB-13. First the arithmetic without a database: the worked examples, the price-change
# scenarios S1 to S6 and random event sequences (property tests). Then the same rules against PostgreSQL.


def money(value):
    return Decimal(value).quantize(Decimal("0.01"))


def apply_event(balance, new_entitled=None, *, paid=None):
    """Apply one event and the event-processing rule: a refund chain of H - E whenever H > E."""
    balance = PaymentBalance(money(paid) if paid is not None else balance.paid,
                             money(new_entitled) if new_entitled is not None else balance.entitled,
                             balance.chains)
    due = balance.refund_due()
    if due:
        require_within_refund_cap(balance, due)
        balance = balance.with_new_chain(RefundChain(f"RFD-{len(balance.chains) + 1}", due, "initiated"))
    assert balance.is_balanced()
    return balance, due


def with_chain_status(balance, root_id, status):
    chains = tuple(RefundChain(c.root_id, c.amount, status) if c.root_id == root_id else c for c in balance.chains)
    return PaymentBalance(balance.paid, balance.entitled, chains)


def test_worked_example_a_failed_refund_with_retry_and_two_causes():
    paid = PaymentBalance(money("5998.00"), money("5998.00"))
    assert paid.refund_due() == 0 and paid.is_balanced()
    reduced, refund = apply_event(paid, "3999.00")  # CHG-51
    assert refund == money("1999.00")
    reduced = with_chain_status(reduced, "RFD-1", "completed")
    cancelled, refund = apply_event(reduced, "0.00")
    assert refund == money("3999.00")
    failed = with_chain_status(cancelled, "RFD-2", "failed")
    assert (failed.refunded, failed.pending, failed.outstanding) == (money("1999.00"), 0, money("3999.00"))
    assert failed.held == 0 and failed.is_balanced()
    with pytest.raises(PaymentBalanceError):  # any further chain for another cause is blocked by the cap
        require_within_refund_cap(failed, money("0.01"))


def test_worked_example_b_payment_without_order():
    balance, refund = apply_event(PaymentBalance(money("3999.00"), money("0.00")))
    assert refund == money("3999.00")
    assert balance.held == 0


@pytest.mark.parametrize("name, events, refunds_created, collect", [
    # each event is a new E, or ("paid", P) for a collection that succeeded
    ("S1 increase, collection succeeds, reduction", [150, ("paid", 150), 120], ["30.00"], "0.00"),
    ("S2 increase, collection fails, reduction still owed", [150, 120], [], "20.00"),
    ("S3 increase unpaid, reduction makes a refund due", [150, 80], ["20.00"], "0.00"),
    ("S4a increase unpaid, then cancellation", [150, 0], ["100.00"], "0.00"),
    ("S4b collection succeeded, then cancellation", [150, ("paid", 150), 0], ["150.00"], "0.00"),
    ("S5 refunds still pending count against later ones", [70, 50, 90], ["30.00", "20.00"], "40.00"),
])
def test_price_change_scenarios(name, events, refunds_created, collect):
    balance = PaymentBalance(money(100), money(100))
    created = []
    for event in events:
        if isinstance(event, tuple):
            balance, due = apply_event(balance, paid=event[1])
        else:
            balance, due = apply_event(balance, event)
        if due:
            created.append(due)
    assert created == [money(amount) for amount in refunds_created]
    assert balance.to_collect == money(collect)


def test_s6_a_retry_stays_in_its_chain():
    records = [
        {"refund_id": "A", "amount": money(30), "refund_status": "failed", "retry_of": None},
        {"refund_id": "B", "amount": money(20), "refund_status": "completed", "retry_of": None},
        {"refund_id": "A2", "amount": money(30), "refund_status": "completed", "retry_of": "A"},
    ]
    balance = PaymentBalance(money(100), money(90), group_refund_chains(records))
    assert [(chain.root_id, chain.status) for chain in balance.chains] == [("A", "completed"), ("B", "completed")]
    assert balance.refund_obligations == money(50)  # 80 would count the retry as a new obligation
    assert balance.to_collect == money(40)
    assert balance.is_balanced()


def test_a_retry_with_another_amount_is_refused():
    records = [
        {"refund_id": "A", "amount": money(30), "refund_status": "failed", "retry_of": None},
        {"refund_id": "A2", "amount": money(31), "refund_status": "initiated", "retry_of": "A"},
    ]
    with pytest.raises(PaymentBalanceError):
        group_refund_chains(records)


def test_a_retry_of_a_record_outside_the_scope_is_refused():
    with pytest.raises(PaymentBalanceError):
        group_refund_chains([{"refund_id": "A2", "amount": money(30), "refund_status": "initiated", "retry_of": "A"}])


@pytest.mark.parametrize("amount", ["0.00", "-1.00", "1999.01"])
def test_the_cap_refuses_zero_negative_and_too_large_refunds(amount):
    balance = PaymentBalance(money("5998.00"), money("3999.00"))
    with pytest.raises(PaymentBalanceError):
        require_within_refund_cap(balance, money(amount))
    require_within_refund_cap(balance, money("1999.00"))


def test_held_money_above_the_entitlement_is_unbalanced():
    assert not PaymentBalance(money(100), money(80)).is_balanced()  # a refund of 20 is missing
    assert not PaymentBalance(money(100), money(0), (RefundChain("A", money(120), "initiated"),)).is_balanced()


def test_scope_is_an_order_or_a_checkout():
    assert str(BalanceScope.for_order("ORD-1")) == "order ORD-1"
    assert str(BalanceScope.for_checkout("CHK-1")) == "checkout CHK-1"
    for bad in ({}, {"order_id": "ORD-1", "checkout_key": "CHK-1"}):
        with pytest.raises(ValueError):
            BalanceScope(**bad)


def random_amount(rng, top):
    return money(Decimal(rng.randint(0, int(top * 100))) / 100)


@pytest.mark.parametrize("seed", range(300))
def test_random_event_sequences_keep_the_books_balanced(seed):
    # Property: after any sequence of price changes, collections and refund outcomes, processed by the rule,
    # P - R = E - C with C >= 0, R never exceeds P, and every new chain is exactly H before minus E after.
    rng = random.Random(seed)
    start = random_amount(rng, 20000) + money("1.00")
    balance = PaymentBalance(start, start)
    for _ in range(40):
        event = rng.choice(["reduce", "raise", "collect", "refund_result", "retry", "cancel"])
        obligations_before = balance.refund_obligations
        held_before = balance.held
        if event == "reduce":
            balance, due = apply_event(balance, random_amount(rng, balance.entitled))
        elif event == "raise":
            balance, due = apply_event(balance, balance.entitled + random_amount(rng, 5000))
        elif event == "cancel":
            balance, due = apply_event(balance, 0)
        elif event == "collect":
            succeeded = balance.to_collect > 0 and rng.random() < 0.6
            balance, due = apply_event(balance, paid=balance.paid + balance.to_collect if succeeded else None)
        else:
            wanted = "initiated" if event == "refund_result" else "failed"
            open_chains = [chain for chain in balance.chains if chain.status == wanted]
            if open_chains:
                chain = rng.choice(open_chains)
                new_status = rng.choice(["completed", "failed"]) if event == "refund_result" else "initiated"
                balance = with_chain_status(balance, chain.root_id, new_status)
            balance, due = apply_event(balance)
            if event == "retry":
                assert balance.refund_obligations == obligations_before  # a retry adds no obligation
        if due:
            assert due == held_before - balance.entitled
        assert balance.paid - balance.refund_obligations == balance.entitled - balance.to_collect
        assert balance.to_collect >= 0
        assert balance.refund_obligations <= balance.paid


# --- against PostgreSQL ---

CLOCK = FixedClock(datetime(2026, 10, 24, 12, 0, tzinfo=IST))
ORDER_SCOPE = BalanceScope.for_order("ORD-T1")


@pytest.fixture
def paid_order(committing_database):
    """ORD-T1 paid in full (worked example A: 5998.00)."""
    with committing_database.begin() as connection:
        connection.execute(insert(orders), order_row("ORD-T1", checkout_key="CHK-T1", net_amount_due="5998.00"))
        connection.execute(insert(payment_attempts),
                           payment_row("PAY-T1", checkout_key="CHK-T1", amount="5998.00", order_id="ORD-T1"))
    return committing_database


def change_entitlement(transaction, amount):
    transaction.connection.execute(
        update(orders).where(orders.c.order_id == "ORD-T1").values(net_amount_due=Decimal(amount)))


def balance_of(engine, scope=ORDER_SCOPE):
    with engine.connect() as connection:
        return load_payment_balance(connection, scope)


@pytest.mark.integration
def test_a_reduction_and_a_cancellation_each_get_one_refund_chain(paid_order):
    with open_transaction(paid_order, CLOCK) as transaction:
        change_entitlement(transaction, "3999.00")
        modification_refund = create_refund_if_due(transaction, ORDER_SCOPE, "modification", "CHG-T1")
    with open_transaction(paid_order, CLOCK) as transaction:
        change_entitlement(transaction, "0.00")
        cancellation_refund = create_refund_if_due(transaction, ORDER_SCOPE, "cancellation", "ORD-T1")
    with paid_order.connect() as connection:
        created = connection.execute(
            select(refunds.c.refund_id, refunds.c.payment_id, refunds.c.source_type, refunds.c.amount,
                   refunds.c.refund_status, refunds.c.created_at).order_by(refunds.c.created_at, refunds.c.refund_id)
        ).all()
    assert [row[:5] for row in created] == [
        (modification_refund, "PAY-T1", "modification", Decimal("1999.00"), "initiated"),
        (cancellation_refund, "PAY-T1", "cancellation", Decimal("3999.00"), "initiated"),
    ]
    assert all(row.created_at == CLOCK.now() for row in created)
    final = balance_of(paid_order)
    assert (final.paid, final.refund_obligations, final.entitled, final.to_collect) == (
        Decimal("5998.00"), Decimal("5998.00"), Decimal("0.00"), Decimal("0.00"))


@pytest.mark.integration
def test_no_refund_when_nothing_is_held_above_the_entitlement(paid_order):
    with open_transaction(paid_order, CLOCK) as transaction:
        assert create_refund_if_due(transaction, ORDER_SCOPE, "modification", "CHG-T1") is None
    assert balance_of(paid_order).chains == ()


@pytest.mark.integration
def test_a_payment_with_no_order_is_refunded_in_full(committing_database):
    # worked example B: the hold lapsed and stock was gone when the payment was confirmed
    with committing_database.begin() as connection:
        connection.execute(insert(payment_attempts), payment_row("PAY-T2", checkout_key="CHK-T2", amount="3999.00"))
    scope = BalanceScope.for_checkout("CHK-T2")
    with open_transaction(committing_database, CLOCK) as transaction:
        refund_id = create_refund_if_due(transaction, scope, "stock_unavailable", "CHK-T2")
    balance = balance_of(committing_database, scope)
    assert [(chain.root_id, chain.amount) for chain in balance.chains] == [(refund_id, Decimal("3999.00"))]
    assert balance.held == 0


@pytest.mark.integration
def test_a_retried_refund_is_counted_once(paid_order):
    with paid_order.begin() as connection:
        connection.execute(update(orders).values(net_amount_due=Decimal("0.00")))
        connection.execute(insert(refunds), refund_row("RFD-T1", amount="5998.00", status="failed", order_id="ORD-T1",
                                                       payment_id="PAY-T1"))
        connection.execute(insert(refunds), refund_row("RFD-T2", amount="5998.00", status="completed",
                                                       order_id="ORD-T1", payment_id="PAY-T1", retry_of="RFD-T1"))
    balance = balance_of(paid_order)
    assert (balance.refund_obligations, balance.refunded, balance.is_balanced()) == (
        Decimal("5998.00"), Decimal("5998.00"), True)
    with open_transaction(paid_order, CLOCK) as transaction:
        # the cap leaves nothing for another cause
        assert create_refund_if_due(transaction, ORDER_SCOPE, "return", "RET-T1") is None


@pytest.mark.integration
def test_an_unbalanced_transaction_is_rolled_back(paid_order):
    with pytest.raises(PaymentBalanceError), open_transaction(paid_order, CLOCK) as transaction:
        change_entitlement(transaction, "3999.00")  # E falls, but no refund is created
        require_balanced_payments(transaction, ORDER_SCOPE)
    assert balance_of(paid_order).entitled == Decimal("5998.00")


@pytest.mark.integration
def test_an_unknown_refund_source_is_refused(paid_order):
    with pytest.raises(ValueError), open_transaction(paid_order, CLOCK) as transaction:
        create_refund_if_due(transaction, ORDER_SCOPE, "goodwill", "X")


@pytest.mark.integration
def test_an_unknown_order_has_no_balance(test_database):
    with test_database.connect() as connection, pytest.raises(LookupError):
        load_payment_balance(connection, BalanceScope.for_order("ORD-NOPE"))


@pytest.mark.integration
def test_two_causes_settled_at_once_refund_the_excess_only_once(paid_order):
    # E fell to 3999.00 outside a transaction (as if two events landed together); both causes are settled at once
    with paid_order.begin() as connection:
        connection.execute(update(orders).values(net_amount_due=Decimal("3999.00")))
    barrier = Barrier(2)

    def refund_cause(source_ref):
        barrier.wait()
        with open_transaction(paid_order, CLOCK) as transaction:
            return create_refund_if_due(transaction, ORDER_SCOPE, "modification", source_ref)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(refund_cause, ["CHG-T1", "CHG-T2"]))
    assert sum(result is not None for result in results) == 1
    assert balance_of(paid_order).refund_obligations == Decimal("1999.00")
