import re
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select

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


def test_unique_under_concurrent_calls(test_database):
    def take(_):
        with test_database.connect() as connection:
            return [next_id(connection, "order") for _ in range(25)]

    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = [order_id for batch in pool.map(take, range(8)) for order_id in batch]
    assert len(ids) == 200
    assert len(set(ids)) == 200
