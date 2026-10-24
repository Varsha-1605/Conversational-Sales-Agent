from enum import StrEnum
import json

from sqlalchemy import func, select

# DB-30, ARCHITECTURE 5.6, API 4: the live stream is driven by PostgreSQL NOTIFY. A notification is sent inside the
# transaction, and PostgreSQL delivers it only when that transaction commits, never when it rolls back, so a
# listener is never told about a change that did not happen. Payloads carry an event name and an ID only; the page
# reads the details through the normal routes.


class Event(StrEnum):
    JOURNEY_MOVED = "journey_moved"
    ORDER_UPDATE = "order_update"
    RESERVATION_UPDATE = "reservation_update"
    ESCALATION_MESSAGE = "escalation_message"
    QUEUE_UPDATE = "queue_update"
    KIOSK_ALERT = "kiosk_alert"


def customer_channel(customer_id) -> str:
    return f"customer:{customer_id}"


def store_channel(store_id) -> str:
    return f"store:{store_id}"


def send_live_update(connection, channel, event: Event, reference):
    """Queue one notification in the current transaction; it is sent if and when the transaction commits."""
    payload = json.dumps({"event": Event(event).value, "reference": reference})
    connection.execute(select(func.pg_notify(channel, payload)))


def notify_customer(connection, customer_id, event: Event, reference):
    send_live_update(connection, customer_channel(customer_id), event, reference)


def notify_store(connection, store_id, event: Event, reference):
    send_live_update(connection, store_channel(store_id), event, reference)
