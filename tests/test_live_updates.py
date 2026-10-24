import json

import psycopg
import pytest

from backend.db.connect_postgres import postgres_url
from backend.domain.live_updates import Event, customer_channel, notify_customer, notify_store, store_channel
from backend.domain.transaction import open_transaction

pytestmark = pytest.mark.integration

WAIT_SECONDS = 1.0


@pytest.fixture
def listener(settings, test_database_name, test_database):
    """A separate connection listening on CUST-0002's and LOC-S01's channels, like the live stream will."""
    url = postgres_url(test_database_name, settings).set(drivername="postgresql")
    with psycopg.connect(url.render_as_string(hide_password=False), autocommit=True) as connection:
        for channel in (customer_channel("CUST-0002"), store_channel("LOC-S01")):
            connection.execute(f'LISTEN "{channel}"')
        yield connection


def notifications_received(listener, timeout=WAIT_SECONDS):
    return [(note.channel, json.loads(note.payload)) for note in listener.notifies(timeout=timeout)]


def test_a_committed_transaction_notifies_the_customer_and_the_store(test_database, listener):
    with open_transaction(test_database) as transaction:
        notify_customer(transaction.connection, "CUST-0002", Event.ORDER_UPDATE, "ORD-000001")
        notify_store(transaction.connection, "LOC-S01", Event.QUEUE_UPDATE, "ORD-000001")
        assert notifications_received(listener, timeout=0.3) == []  # nothing is sent before the commit
    assert notifications_received(listener) == [
        ("customer:CUST-0002", {"event": "order_update", "reference": "ORD-000001"}),
        ("store:LOC-S01", {"event": "queue_update", "reference": "ORD-000001"}),
    ]


def test_a_rolled_back_transaction_does_not_notify(test_database, listener):
    with pytest.raises(RuntimeError), open_transaction(test_database) as transaction:
        notify_customer(transaction.connection, "CUST-0002", Event.ORDER_UPDATE, "ORD-000001")
        raise RuntimeError("the change failed")
    assert notifications_received(listener) == []


def test_other_customers_channels_are_not_heard(test_database, listener):
    with open_transaction(test_database) as transaction:
        notify_customer(transaction.connection, "CUST-0009", Event.ORDER_UPDATE, "ORD-000002")
    assert notifications_received(listener) == []


def test_an_unknown_event_is_refused(test_database):
    with pytest.raises(ValueError), open_transaction(test_database) as transaction:
        notify_customer(transaction.connection, "CUST-0002", "something_happened", "ORD-000001")
