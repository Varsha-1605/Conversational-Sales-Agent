from sqlalchemy import update

from backend.db.schema import journeys

# ARCHITECTURE 7.3: only one channel at a time may change a customer's journey. When the customer signs out of the
# channel that holds it, the journey has no active channel, and the next channel opened becomes active without a prompt.


def clear_active_channel(transaction, customer_id: str, channel: str) -> bool:
    """Empty the journey's active channel if `channel` holds it; returns whether it did. If another channel holds
    it, nothing changes."""
    result = transaction.connection.execute(update(journeys).where(
        journeys.c.customer_id == customer_id, journeys.c.active_channel == channel).values(active_channel=None))
    return result.rowcount == 1
