from dataclasses import dataclass
from decimal import Decimal
import logging

from sqlalchemy import and_, desc, func, insert, select

from backend.db.schema import REFUND_SOURCES, orders, payment_attempts, refunds
from backend.domain.database_locks import lock_settlement
from backend.domain.ids import next_id
from backend.domain.money import to_money

# DATA_SPEC 4.2 and 4.4 ("reconciliation"), DB-13, NF-10. For one scope (an order, or a checkout for which no order
# was made):
#   P  paid: captured payment attempts             R  refund obligations: every refund chain, counted once
#   H  held = P - R                                E  entitled to keep: orders.net_amount_due (0 with no order)
#   C  to collect = max(0, E - H)
# After any event that changes E, and after any payment in the scope succeeds, a refund chain of H - E is created
# when H > E. Before commit the transaction asserts P - R = E - C and R <= P; a failure rolls it back.
# A refund is never worked out as a difference of E values (S2 in DATA_SPEC 4.4.2 shows why).

logger = logging.getLogger(__name__)

ZERO = Decimal("0.00")


class PaymentBalanceError(Exception):
    """The money records of a scope do not add up, or a refund would break the cap. The transaction is rolled back."""


@dataclass(frozen=True)
class BalanceScope:
    """An order, or a checkout_key whose payment produced no order. Exactly one is set."""

    order_id: str | None = None
    checkout_key: str | None = None

    def __post_init__(self):
        if (self.order_id is None) == (self.checkout_key is None):
            raise ValueError("a balance scope is an order or a checkout, not both or neither")

    @classmethod
    def for_order(cls, order_id):
        return cls(order_id=order_id)

    @classmethod
    def for_checkout(cls, checkout_key):
        return cls(checkout_key=checkout_key)

    def __str__(self):
        return f"order {self.order_id}" if self.order_id else f"checkout {self.checkout_key}"


@dataclass(frozen=True)
class RefundChain:
    """One refund record plus its retries: the first record's amount, the latest record's status."""

    root_id: str
    amount: Decimal
    status: str


@dataclass(frozen=True)
class PaymentBalance:
    paid: Decimal
    entitled: Decimal
    chains: tuple[RefundChain, ...] = ()

    def _sum_chain_amounts(self, *statuses) -> Decimal:
        return sum((chain.amount for chain in self.chains if not statuses or chain.status in statuses), ZERO)

    @property
    def refund_obligations(self) -> Decimal:  # R = refunded + pending + outstanding
        return self._sum_chain_amounts()

    @property
    def refunded(self) -> Decimal:
        return self._sum_chain_amounts("completed")

    @property
    def pending(self) -> Decimal:
        return self._sum_chain_amounts("initiated")

    @property
    def outstanding(self) -> Decimal:
        return self._sum_chain_amounts("failed")

    @property
    def held(self) -> Decimal:
        return self.paid - self.refund_obligations

    @property
    def to_collect(self) -> Decimal:
        return max(ZERO, self.entitled - self.held)

    def refund_due(self) -> Decimal:
        """H - E when the business holds more than it is entitled to keep; otherwise 0."""
        return max(ZERO, self.held - self.entitled)

    def is_balanced(self) -> bool:
        return (self.paid - self.refund_obligations == self.entitled - self.to_collect
                and self.refund_obligations <= self.paid)

    def with_new_chain(self, chain: RefundChain) -> "PaymentBalance":
        return PaymentBalance(self.paid, self.entitled, (*self.chains, chain))


def require_within_refund_cap(balance: PaymentBalance, amount: Decimal):
    """A new chain is allowed only if R + a <= P - E (the cumulative refund cap). Raises PaymentBalanceError."""
    if amount <= ZERO:
        raise PaymentBalanceError(f"a refund must be above zero, not {amount}")
    if balance.refund_obligations + amount > balance.paid - balance.entitled:
        raise PaymentBalanceError(
            f"refund of {amount} breaks the cap: R {balance.refund_obligations}, P {balance.paid}, "
            f"E {balance.entitled}")


def group_refund_chains(records) -> tuple[RefundChain, ...]:
    """Group refund records (refund_id, amount, refund_status, retry_of) into chains, counting retries once.
    Raises PaymentBalanceError for a retry whose amount differs from its chain's, or that retries a record
    outside the given records."""
    by_id = {record["refund_id"]: record for record in records}
    retry_of = {record["retry_of"]: record for record in records if record["retry_of"] is not None}
    chains = []
    for root in (record for record in records if record["retry_of"] is None):
        latest = root
        while latest["refund_id"] in retry_of:
            latest = retry_of[latest["refund_id"]]
            if latest["amount"] != root["amount"]:
                raise PaymentBalanceError(
                    f"refund {latest['refund_id']} retries {root['refund_id']} with another amount")
        chains.append(RefundChain(root["refund_id"], to_money(root["amount"]), latest["refund_status"]))
    orphans = [refund_id for refund_id, record in by_id.items()
               if record["retry_of"] is not None and record["retry_of"] not in by_id]
    if orphans:
        raise PaymentBalanceError(f"refunds {', '.join(orphans)} retry a record outside their scope")
    return tuple(chains)


def _payments_in_scope(scope):
    if scope.order_id:
        return payment_attempts.c.order_id == scope.order_id
    return and_(payment_attempts.c.checkout_key == scope.checkout_key, payment_attempts.c.order_id.is_(None))


def _refunds_in_scope(scope):
    if scope.order_id:
        return refunds.c.order_id == scope.order_id
    return and_(refunds.c.order_id.is_(None), refunds.c.source_type == "stock_unavailable",
                refunds.c.source_ref == scope.checkout_key)


def load_payment_balance(connection, scope: BalanceScope) -> PaymentBalance:
    """P, the refund chains and E of one scope, read from the database. Raises LookupError for an unknown order."""
    entitled = ZERO
    if scope.order_id:
        entitled = connection.execute(
            select(orders.c.net_amount_due).where(orders.c.order_id == scope.order_id)).scalar_one_or_none()
        if entitled is None:
            raise LookupError(f"unknown order {scope.order_id}")
    paid = connection.execute(
        select(func.coalesce(func.sum(payment_attempts.c.amount), ZERO))
        .where(_payments_in_scope(scope), payment_attempts.c.outcome == "success")
    ).scalar_one()
    records = connection.execute(
        select(refunds.c.refund_id, refunds.c.amount, refunds.c.refund_status, refunds.c.retry_of)
        .where(_refunds_in_scope(scope))
    ).mappings().all()
    return PaymentBalance(to_money(paid), to_money(entitled), group_refund_chains(records))


def _latest_successful_payment_id(connection, scope):
    return connection.execute(
        select(payment_attempts.c.payment_id)
        .where(_payments_in_scope(scope), payment_attempts.c.outcome == "success")
        .order_by(desc(payment_attempts.c.created_at)).limit(1)
    ).scalar_one_or_none()


def require_balanced_payments(transaction, scope: BalanceScope):
    """Add the commit check for a scope: P - R = E - C and R <= P (NF-10)."""

    def check_balanced(connection):
        balance = load_payment_balance(connection, scope)
        if not balance.is_balanced():
            logger.error("payment balance check failed for %s: P %s, R %s, E %s", scope, balance.paid,
                         balance.refund_obligations, balance.entitled)
            raise PaymentBalanceError(f"money records of {scope} do not add up")

    transaction.add_commit_check(check_balanced)


def create_refund_if_due(transaction, scope: BalanceScope, source_type, source_ref) -> str | None:
    """Process an event that changed E (or a payment that succeeded) for one scope: create one refund chain of
    H - E if the business now holds more than it is entitled to keep, and check the scope balances before commit.
    Returns the new refund_id, or None when no refund is due. The refund starts as `initiated`; the Payment agent
    executes it. Raises PaymentBalanceError if the cap would be broken, ValueError for an unknown source_type."""
    if source_type not in REFUND_SOURCES:
        raise ValueError(f"unknown refund source {source_type!r}")
    connection = transaction.connection
    lock_settlement(connection, scope.order_id, scope.checkout_key)
    require_balanced_payments(transaction, scope)
    balance = load_payment_balance(connection, scope)
    amount = balance.refund_due()
    if amount == ZERO:
        return None
    require_within_refund_cap(balance, amount)
    refund_id = next_id(connection, "refund")
    connection.execute(insert(refunds).values(
        refund_id=refund_id, order_id=scope.order_id,
        payment_id=_latest_successful_payment_id(connection, scope), source_type=source_type,
        source_ref=source_ref, amount=amount, refund_status="initiated", retry_of=None,
        created_at=transaction.clock.now(),
    ))
    return refund_id
