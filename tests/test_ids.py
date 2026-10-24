import re
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select, text

from backend.db.schema import ID_SEQUENCES, customers
from backend.domain.ids import FORMATS, next_id

pytestmark = pytest.mark.integration


def test_every_kind_has_a_sequence():
    assert FORMATS.keys() == ID_SEQUENCES.keys()


@pytest.mark.parametrize("kind", FORMATS)
def test_format(test_database, kind):
    prefix, digits = FORMATS[kind]
    with test_database.connect() as connection:
        assert re.fullmatch(rf"{prefix}-\d{{{digits},}}", next_id(connection, kind))


def test_new_customers_continue_after_the_seed(test_database):
    with test_database.connect() as connection:
        seeded = set(connection.execute(select(customers.c.customer_id)).scalars())
        new = next_id(connection, "customer")
    assert new not in seeded
    assert int(new.removeprefix("CUST-")) > max(int(cid.removeprefix("CUST-")) for cid in seeded)


def test_migrations_create_exactly_the_sequences_the_schema_declares(test_database):
    # Alembic does not compare sequences, so nothing else would notice the migration and schema.py drifting
    with test_database.connect() as connection:
        created = dict(connection.execute(text(
            "SELECT sequencename, start_value FROM pg_sequences WHERE schemaname = 'public'"
        )).all())
    declared = {sequence.name: sequence.start for sequence in ID_SEQUENCES.values()}
    assert created == declared


def test_the_seed_moves_the_customer_sequence_past_the_seeded_customers(test_database, seed_test_database):
    with test_database.connect() as connection:
        connection.execute(text("SELECT setval('customer_id_seq', 1)"))  # as if the seed file had grown
        connection.commit()
    seed_test_database()
    with test_database.connect() as connection:
        seeded = max(int(cid.removeprefix("CUST-")) for cid in connection.execute(select(customers.c.customer_id)).scalars())
        assert int(next_id(connection, "customer").removeprefix("CUST-")) > seeded


def test_unique_under_concurrent_calls(test_database):
    def take(_):
        with test_database.connect() as connection:
            return [next_id(connection, "order") for _ in range(25)]

    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = [order_id for batch in pool.map(take, range(8)) for order_id in batch]
    assert len(ids) == 200
    assert len(set(ids)) == 200
