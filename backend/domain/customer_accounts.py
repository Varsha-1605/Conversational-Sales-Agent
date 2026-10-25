from sqlalchemy import insert

from backend.db.schema import customers, journeys
from backend.domain.ids import next_id

# Sign-up (API 3.1, P-16, U-18): a new customer starts on the Base tier with no points, device preference web, and
# both consent choices off. Each customer has one journey row from the start (DATA_SPEC 4.1, DB-35).


def create_customer_account(transaction, full_name: str, email: str, password_hash: str) -> str:
    """Add a customer and their empty journey inside the caller's transaction; returns the new customer_id.
    `email` is lower case. Raises sqlalchemy.exc.IntegrityError if another customer has it."""
    connection = transaction.connection
    customer_id = next_id(connection, "customer")
    connection.execute(insert(customers).values(
        customer_id=customer_id, full_name=full_name, email=email, password_hash=password_hash,
        loyalty_tier="Base", points_balance=0, device_preference="web", personalization_allowed=False))
    connection.execute(insert(journeys).values(customer_id=customer_id))
    return customer_id
