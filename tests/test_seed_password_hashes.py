import pytest
from sqlalchemy import select, update

from backend.auth.passwords import verify_password
from backend.db.schema import customers
from tests.signed_in_app import DEMO_CUSTOMERS

pytestmark = pytest.mark.integration

WITHOUT_HASH, WITH_HASH = DEMO_CUSTOMERS[2], DEMO_CUSTOMERS[3]
EXISTING_HASH = "$argon2id$an-earlier-hash-the-seed-must-leave-alone"


def stored_hash(engine, customer_id):
    with engine.connect() as connection:
        return connection.execute(select(customers.c.password_hash).where(
            customers.c.customer_id == customer_id)).scalar_one()


def set_hash(engine, customer_id, password_hash):
    with engine.begin() as connection:
        connection.execute(update(customers).where(
            customers.c.customer_id == customer_id).values(password_hash=password_hash))


def test_the_seed_fills_in_a_missing_hash_and_never_replaces_an_existing_one(
        committing_database, seed_test_database):
    original = {row["customer_id"]: stored_hash(committing_database, row["customer_id"])
                for row in (WITHOUT_HASH, WITH_HASH)}
    try:
        set_hash(committing_database, WITHOUT_HASH["customer_id"], None)  # a customer seeded before hashing existed
        set_hash(committing_database, WITH_HASH["customer_id"], EXISTING_HASH)

        seed_test_database()

        filled = stored_hash(committing_database, WITHOUT_HASH["customer_id"])
        assert filled.startswith("$argon2id$") and verify_password(WITHOUT_HASH["password"], filled)
        assert stored_hash(committing_database, WITH_HASH["customer_id"]) == EXISTING_HASH
    finally:
        for customer_id, password_hash in original.items():
            set_hash(committing_database, customer_id, password_hash)
