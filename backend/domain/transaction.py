from contextlib import contextmanager
import functools

from sqlalchemy.sql import Delete, Insert, Select, Update

from backend.domain.clock import Clock
from backend.domain.ids import next_id

# DB-41, ARCHITECTURE 5.6 ("unit of work"): the domain core is the only writer. Every confirmed action, checkout step
# and expiry step is one PostgreSQL transaction, opened here, committed here, and rolled back as a whole if anything
# fails.
#
# The one exception is a payment attempt: it is recorded in a transaction of its own, opened and committed before the
# transaction that creates the order, so a decline or timeout is never lost when the order rolls back (A 2.3).

# the statements a worker's write operation may run: no COMMIT, ROLLBACK, BEGIN or other raw SQL
ALLOWED_WORKER_STATEMENTS = (Select, Insert, Update, Delete)


class WriteOutsideTransaction(RuntimeError):
    """A write operation was called directly, or after its transaction ended."""


class Transaction:
    """One open PostgreSQL transaction. Created only by open_transaction()."""

    def __init__(self, connection, clock):
        self.connection = connection
        self.clock = clock
        self.is_open = True
        self._commit_checks = []

    def add_commit_check(self, check):
        """Run check(connection) after the action's writes and before the commit. A check raises to refuse the
        commit, which rolls the whole transaction back (payment balance, NF-10)."""
        self._commit_checks.append(check)

    def run_write_operation(self, operation, *args, **kwargs):
        """Call a worker's write operation inside this transaction (A 5.5). Raises TypeError for any other function."""
        if getattr(operation, "effect", None) != "write":
            raise TypeError(f"{operation!r} is not a write operation")
        return operation(WorkerConnection(self), *args, **kwargs)

    def _run_commit_checks(self):
        for check in self._commit_checks:
            check(self.connection)


class WorkerConnection:
    """What a worker's write operation receives: its statements run inside the open transaction, it can take IDs and
    read the transaction's clock, and it has no way to open, commit or roll back a transaction (AGENTS 4)."""

    def __init__(self, transaction):
        self._transaction = transaction

    @property
    def is_open(self):
        return self._transaction.is_open

    @property
    def clock(self):
        return self._transaction.clock

    def execute(self, statement, parameters=None):
        """Run one SELECT, INSERT, UPDATE or DELETE built with SQLAlchemy. Raises TypeError for anything else."""
        self._require_open()
        if not isinstance(statement, ALLOWED_WORKER_STATEMENTS):
            raise TypeError("a write operation may run only SELECT, INSERT, UPDATE or DELETE statements")
        return self._transaction.connection.execute(statement, parameters)

    def next_id(self, kind):
        self._require_open()
        return next_id(self._transaction.connection, kind)

    def _require_open(self):
        if not self.is_open:
            raise WriteOutsideTransaction("the transaction has ended")


def write_operation(function):
    """Marks a worker operation whose effect is `write` (A 5.5). It runs only through
    Transaction.run_write_operation, which passes a WorkerConnection as the first argument.
    Raises WriteOutsideTransaction when called any other way."""

    @functools.wraps(function)
    def run_inside_transaction_only(connection, *args, **kwargs):
        if not isinstance(connection, WorkerConnection) or not connection.is_open:
            raise WriteOutsideTransaction(f"{function.__qualname__} runs only inside the domain core's transaction")
        return function(connection, *args, **kwargs)

    run_inside_transaction_only.effect = "write"
    return run_inside_transaction_only


@contextmanager
def open_transaction(engine, clock=None):
    """Open one transaction on its own connection. It commits when the block ends normally and every commit check
    passes; any exception rolls everything back and is raised again."""
    with engine.connect() as connection, connection.begin():
        transaction = Transaction(connection, clock or Clock())
        try:
            yield transaction
            transaction._run_commit_checks()
        finally:
            transaction.is_open = False
