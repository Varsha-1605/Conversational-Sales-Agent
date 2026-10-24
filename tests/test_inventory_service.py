from datetime import timedelta
from time import sleep

import pytest
from sqlalchemy import func, insert, select

from agents.workers.inventory import stock_operations, stock_rules
from agents.workers.inventory.contract import (
    AvailabilityRequest, CommitHoldRequest, CreateHoldRequest, FulfillmentOptionsRequest, ReleaseHoldRequest,
)
from backend.db.schema import stock_holds
from backend.domain.clock import FixedClock
from backend.domain.status_changes import StatusChangeNotAllowed
from backend.domain.transaction import open_transaction
from tests.race_winners import RACE_WINDOW_SECONDS, count_race_winners
from tests.sample_rows import hold_row

pytestmark = pytest.mark.integration

# Stock in the seed (data/raw/inventory_levels.csv) used below, as on-hand quantity:
#   SKU-1002-M-BLU: LOC-S02 2, LOC-S05 3     SKU-2001-8-BRO: LOC-S01 1 (the last unit)
#   SKU-4001-L-IVO: LOC-W01 7, LOC-S03 1 (the one stale record)
SHIRT_WITH_TWO = ("SKU-1002-M-BLU", "LOC-S02")
LAST_UNIT = ("SKU-2001-8-BRO", "LOC-S01")


@pytest.fixture
def clock(stock_data_now):
    return FixedClock(stock_data_now)


def check_availability(engine, sql_server, sku_and_location, at):
    sku, location_id = sku_and_location
    with engine.connect() as postgres, sql_server.connect() as client:
        request = AvailabilityRequest(sku=sku, location_id=location_id)
        return stock_operations.availability(postgres, client, request, at)


def checkout_hold_request(sku_and_location, quantity=1, reference_id="CHK-T1"):
    sku, location_id = sku_and_location
    return CreateHoldRequest(reference_type="checkout", reference_id=reference_id,
                             lines=[{"sku": sku, "location_id": location_id, "quantity": quantity}])


def hold_stock(engine, sql_server, clock, request):
    with sql_server.connect() as client, open_transaction(engine, clock) as transaction:
        return transaction.run_write_operation(stock_operations.create_hold, client, request)


def change_hold(engine, clock, operation, request):
    with open_transaction(engine, clock) as transaction:
        return transaction.run_write_operation(operation, request)


def count_holds(engine):
    with engine.connect() as connection:
        return connection.execute(select(func.count()).select_from(stock_holds)).scalar_one()


def insert_hold(engine, **fields):
    with engine.begin() as connection:
        connection.execute(insert(stock_holds).values(hold_row(**fields)))


# availability

@pytest.mark.parametrize("sku, location_id, state, quantity", [
    ("SKU-1001-M-WHI", "LOC-W01", "in_stock", 25),
    ("SKU-1002-M-BLU", "LOC-S05", "in_stock", 3),         # one above the low-stock limit
    ("SKU-1002-M-BLU", "LOC-S02", "low_stock", 2),        # exactly the limit
    ("SKU-1002-L-BLU", "LOC-S06", "low_stock", 1),
    ("SKU-1002-L-BLU", "LOC-W01", "out_of_stock", 0),
    ("SKU-1001-M-WHI", "LOC-S03", "out_of_stock", 0),     # no stock record at that store
    ("SKU-4001-L-IVO", "LOC-S03", "unconfirmed", None),   # the one stale record
])
def test_availability_for_each_kind_of_stock(
        test_database, sql_server, stock_data_now, sku, location_id, state, quantity):
    reply = check_availability(test_database, sql_server, (sku, location_id), stock_data_now)
    assert (reply.state, reply.available_quantity) == (state, quantity)


def test_a_missing_stock_record_has_no_update_time(test_database, sql_server, stock_data_now):
    assert check_availability(test_database, sql_server, ("SKU-1001-M-WHI", "LOC-S03"), stock_data_now) \
        .stock_updated_at is None


def test_holds_are_subtracted_but_expired_lapsed_and_released_holds_are_not(
        committing_database, sql_server, stock_data_now):
    sku, location_id = SHIRT_WITH_TWO
    insert_hold(committing_database, hold_id="HLD-T1", sku=sku, location_id=location_id, status="committed")
    assert check_availability(committing_database, sql_server, SHIRT_WITH_TWO, stock_data_now) \
        .available_quantity == 1

    insert_hold(committing_database, hold_id="HLD-T2", sku=sku, location_id=location_id, status="active",
                reference_type="checkout", expires_at=stock_data_now + timedelta(minutes=5))
    reply = check_availability(committing_database, sql_server, SHIRT_WITH_TWO, stock_data_now)
    assert (reply.state, reply.available_quantity) == ("out_of_stock", 0)

    after_expiry = stock_data_now + timedelta(minutes=6)  # the active hold has expired, the committed one has not
    assert check_availability(committing_database, sql_server, SHIRT_WITH_TWO, after_expiry).available_quantity == 1

    insert_hold(committing_database, hold_id="HLD-T3", sku=sku, location_id=location_id, status="lapsed")
    insert_hold(committing_database, hold_id="HLD-T4", sku=sku, location_id=location_id, status="released")
    assert check_availability(committing_database, sql_server, SHIRT_WITH_TWO, after_expiry).available_quantity == 1


# fulfilment options

def available_options(reply):
    return {(option.method, option.location_id) for option in reply.options if option.is_available}


def read_options(engine, sql_server, sku, at, quantity=1):
    with engine.connect() as postgres, sql_server.connect() as client:
        return stock_operations.fulfillment_options(
            postgres, client, FulfillmentOptionsRequest(sku=sku, quantity=quantity), at)


def test_an_item_in_the_warehouse_and_in_stores_has_every_option(test_database, sql_server, stock_data_now):
    reply = read_options(test_database, sql_server, "SKU-1001-M-WHI", stock_data_now)
    assert available_options(reply) == {
        ("ship_to_home", "LOC-W01"),
        ("click_collect", "LOC-S01"), ("click_collect", "LOC-S02"), ("click_collect", "LOC-S04"),
        ("in_store", "LOC-S01"), ("in_store", "LOC-S02"), ("in_store", "LOC-S04"),
    }
    assert [option.method for option in reply.options] == sorted(
        (option.method for option in reply.options), key=("ship_to_home", "click_collect", "in_store").index)


def test_a_warehouse_only_item_can_only_be_shipped(test_database, sql_server, stock_data_now):
    reply = read_options(test_database, sql_server, "SKU-3001-38-NAV", stock_data_now)
    assert available_options(reply) == {("ship_to_home", "LOC-W01")}


def test_a_store_only_item_cannot_be_shipped_and_click_and_collect_needs_a_store_that_offers_it(
        test_database, sql_server, stock_data_now):
    reply = read_options(test_database, sql_server, "SKU-4002-M-MAR", stock_data_now)
    # LOC-S06 holds it but does not offer click & collect
    assert available_options(reply) == {("click_collect", "LOC-S02"), ("in_store", "LOC-S02"), ("in_store", "LOC-S06")}


def test_an_option_is_available_only_where_stock_covers_the_quantity(test_database, sql_server, stock_data_now):
    reply = read_options(test_database, sql_server, "SKU-1001-M-WHI", stock_data_now, quantity=4)
    assert available_options(reply) == {("ship_to_home", "LOC-W01"), ("click_collect", "LOC-S01"),
                                        ("in_store", "LOC-S01")}


def test_a_store_with_stale_stock_is_listed_but_cannot_be_chosen(test_database, sql_server, stock_data_now):
    reply = read_options(test_database, sql_server, "SKU-4001-L-IVO", stock_data_now)
    stale = [option for option in reply.options if option.location_id == "LOC-S03"]
    assert stale and all(option.state == "unconfirmed" and not option.is_available for option in stale)
    assert ("ship_to_home", "LOC-W01") in available_options(reply)


# holds

def test_a_checkout_hold_lasts_fifteen_minutes_and_takes_the_unit_off_the_shelf(
        committing_database, sql_server, clock):
    reply = hold_stock(committing_database, sql_server, clock, checkout_hold_request(SHIRT_WITH_TWO))
    (hold,) = reply.holds
    assert (hold.hold_status, hold.reference_type, hold.reference_id) == ("active", "checkout", "CHK-T1")
    assert hold.expires_at == clock.now() + stock_rules.CHECKOUT_HOLD
    stock = check_availability(committing_database, sql_server, SHIRT_WITH_TWO, clock.now())
    assert (stock.state, stock.available_quantity) == ("low_stock", 1)


def test_a_reservation_hold_lasts_until_the_end_of_the_slot(committing_database, sql_server, clock):
    slot_end = clock.now() + timedelta(days=1, hours=2)
    request = CreateHoldRequest(
        reference_type="reservation", reference_id="RSV-T1", expires_at=slot_end,
        lines=[{"sku": SHIRT_WITH_TWO[0], "location_id": SHIRT_WITH_TWO[1], "quantity": 1}])
    (hold,) = hold_stock(committing_database, sql_server, clock, request).holds
    assert (hold.hold_status, hold.expires_at) == ("active", slot_end)


def test_a_reservation_whose_slot_has_ended_cannot_hold_stock(committing_database, sql_server, clock):
    request = CreateHoldRequest(
        reference_type="reservation", reference_id="RSV-T1", expires_at=clock.now() - timedelta(minutes=1),
        lines=[{"sku": SHIRT_WITH_TWO[0], "location_id": SHIRT_WITH_TWO[1], "quantity": 1}])
    with pytest.raises(ValueError):
        hold_stock(committing_database, sql_server, clock, request)
    assert count_holds(committing_database) == 0


def test_an_order_hold_starts_committed_and_never_lapses(committing_database, sql_server, clock):
    request = CreateHoldRequest(
        reference_type="order", reference_id="ORD-T1",
        lines=[{"sku": SHIRT_WITH_TWO[0], "location_id": SHIRT_WITH_TWO[1], "quantity": 1, "order_line_id": "L1"}])
    (hold,) = hold_stock(committing_database, sql_server, clock, request).holds
    assert (hold.hold_status, hold.expires_at, hold.order_line_id) == ("committed", None, "L1")


def test_an_exchange_hold_starts_committed_on_the_replacement_item(committing_database, sql_server, clock):
    sku, location_id = SHIRT_WITH_TWO
    request = CreateHoldRequest(
        reference_type="exchange", reference_id="RET-T1",
        lines=[{"sku": sku, "location_id": location_id, "quantity": 1, "order_line_id": "L1"}])
    (hold,) = hold_stock(committing_database, sql_server, clock, request).holds
    assert (hold.sku, hold.hold_status, hold.expires_at, hold.order_line_id) == (sku, "committed", None, "L1")


def test_holds_for_several_lines_get_their_own_ids(committing_database, sql_server, clock):
    request = CreateHoldRequest(reference_type="checkout", reference_id="CHK-T1", lines=[
        {"sku": "SKU-1001-M-WHI", "location_id": "LOC-W01", "quantity": 2},
        {"sku": "SKU-2001-9-BRO", "location_id": "LOC-W01", "quantity": 1}])
    holds = hold_stock(committing_database, sql_server, clock, request).holds
    assert len({hold.hold_id for hold in holds}) == 2
    assert count_holds(committing_database) == 2


def test_nothing_is_held_when_one_line_cannot_be_covered(committing_database, sql_server, clock):
    request = CreateHoldRequest(reference_type="checkout", reference_id="CHK-T1", lines=[
        {"sku": "SKU-1001-M-WHI", "location_id": "LOC-W01", "quantity": 1},
        {"sku": "SKU-1002-L-BLU", "location_id": "LOC-W01", "quantity": 1}])  # none left
    with pytest.raises(stock_operations.OutOfStock) as refusal:
        hold_stock(committing_database, sql_server, clock, request)
    assert (refusal.value.sku, refusal.value.available, refusal.value.requested) == ("SKU-1002-L-BLU", 0, 1)
    assert count_holds(committing_database) == 0


def test_two_lines_for_the_same_unit_are_checked_together(committing_database, sql_server, clock):
    sku, location_id = LAST_UNIT
    line = {"sku": sku, "location_id": location_id, "quantity": 1}
    request = CreateHoldRequest(reference_type="checkout", reference_id="CHK-T1", lines=[line, line])
    with pytest.raises(stock_operations.OutOfStock) as refusal:
        hold_stock(committing_database, sql_server, clock, request)
    assert (refusal.value.available, refusal.value.requested) == (1, 2)


def test_stock_already_held_cannot_be_held_again(committing_database, sql_server, clock):
    hold_stock(committing_database, sql_server, clock, checkout_hold_request(SHIRT_WITH_TWO, quantity=2))
    with pytest.raises(stock_operations.OutOfStock):
        hold_stock(committing_database, sql_server, clock,
                   checkout_hold_request(SHIRT_WITH_TWO, reference_id="CHK-T2"))


def test_stale_stock_cannot_be_held(committing_database, sql_server, clock):
    with pytest.raises(stock_operations.StockNotConfirmed):
        hold_stock(committing_database, sql_server, clock, checkout_hold_request(("SKU-4001-L-IVO", "LOC-S03")))
    assert count_holds(committing_database) == 0


def test_a_location_with_no_stock_record_cannot_be_held(committing_database, sql_server, clock):
    with pytest.raises(stock_operations.OutOfStock):
        hold_stock(committing_database, sql_server, clock, checkout_hold_request(("SKU-1001-M-WHI", "LOC-S03")))


def test_two_buyers_of_the_last_unit_exactly_one_gets_the_hold(
        committing_database, sql_server, clock, monkeypatch):
    check_stock = stock_operations._require_enough_stock

    def check_then_pause(*arguments):  # widens the gap between the stock check and the insert
        check_stock(*arguments)
        sleep(RACE_WINDOW_SECONDS)

    monkeypatch.setattr(stock_operations, "_require_enough_stock", check_then_pause)

    def buy():
        try:
            hold_stock(committing_database, sql_server, clock, checkout_hold_request(LAST_UNIT))
        except stock_operations.OutOfStock:
            return False
        return True

    assert count_race_winners(buy) == 1
    assert check_availability(committing_database, sql_server, LAST_UNIT, clock.now()).available_quantity == 0


# the life of a hold: create, commit, release, lapse

def test_payment_commits_the_checkout_hold_and_points_it_at_the_order(committing_database, sql_server, clock):
    (hold,) = hold_stock(committing_database, sql_server, clock, checkout_hold_request(SHIRT_WITH_TWO)).holds
    committed = change_hold(committing_database, clock, stock_operations.commit_hold,
                            CommitHoldRequest(hold_id=hold.hold_id, order_id="ORD-T1", order_line_id="L1"))
    assert (committed.hold_status, committed.reference_type, committed.reference_id, committed.order_line_id,
            committed.expires_at) == ("committed", "order", "ORD-T1", "L1", None)
    # sold stock stays off the shelf, and no longer lapses
    later = clock.now() + timedelta(hours=3)
    assert check_availability(committing_database, sql_server, SHIRT_WITH_TWO, later).available_quantity == 1


def test_a_hold_that_expired_cannot_be_committed_and_its_stock_is_free_again(
        committing_database, sql_server, clock):
    (hold,) = hold_stock(committing_database, sql_server, clock, checkout_hold_request(SHIRT_WITH_TWO)).holds
    after_expiry = FixedClock(clock.now() + stock_rules.CHECKOUT_HOLD + timedelta(seconds=1))
    with pytest.raises(StatusChangeNotAllowed):
        change_hold(committing_database, after_expiry, stock_operations.commit_hold,
                    CommitHoldRequest(hold_id=hold.hold_id, order_id="ORD-T1", order_line_id="L1"))
    assert check_availability(committing_database, sql_server, SHIRT_WITH_TWO, after_expiry.now()) \
        .available_quantity == 2


def test_a_lapsed_hold_never_becomes_committed(committing_database, clock):
    insert_hold(committing_database, hold_id="HLD-T1", sku="SKU-1001-M-WHI", location_id="LOC-W01",
                status="lapsed", reference_type="checkout", reference_id="CHK-T1")
    with pytest.raises(StatusChangeNotAllowed):
        change_hold(committing_database, clock, stock_operations.commit_hold,
                    CommitHoldRequest(hold_id="HLD-T1", order_id="ORD-T1", order_line_id="L1"))


def test_only_a_checkout_hold_is_committed_by_payment(committing_database, clock):
    insert_hold(committing_database, hold_id="HLD-T1", sku="SKU-1001-M-WHI", location_id="LOC-W01",
                status="active", reference_type="reservation", reference_id="RSV-T1",
                expires_at=clock.now() + timedelta(hours=1))
    with pytest.raises(ValueError):
        change_hold(committing_database, clock, stock_operations.commit_hold,
                    CommitHoldRequest(hold_id="HLD-T1", order_id="ORD-T1", order_line_id="L1"))


@pytest.mark.parametrize("starting_status", ["active", "committed"])
def test_releasing_a_hold_gives_the_stock_back(committing_database, sql_server, clock, starting_status):
    sku, location_id = SHIRT_WITH_TWO
    insert_hold(committing_database, hold_id="HLD-T1", sku=sku, location_id=location_id, status=starting_status,
                reference_type="checkout" if starting_status == "active" else "order",
                expires_at=clock.now() + timedelta(minutes=10) if starting_status == "active" else None)
    assert check_availability(committing_database, sql_server, SHIRT_WITH_TWO, clock.now()).available_quantity == 1
    released = change_hold(
        committing_database, clock, stock_operations.release_hold, ReleaseHoldRequest(hold_id="HLD-T1"))
    assert released.hold_status == "released"
    assert check_availability(committing_database, sql_server, SHIRT_WITH_TWO, clock.now()).available_quantity == 2


@pytest.mark.parametrize("final_status", ["released", "lapsed"])
def test_a_released_or_lapsed_hold_cannot_be_released_again(committing_database, clock, final_status):
    insert_hold(committing_database, hold_id="HLD-T1", sku="SKU-1001-M-WHI", location_id="LOC-W01",
                status=final_status)
    with pytest.raises(StatusChangeNotAllowed):
        change_hold(committing_database, clock, stock_operations.release_hold, ReleaseHoldRequest(hold_id="HLD-T1"))


def test_an_unknown_hold_cannot_be_changed(committing_database, clock):
    with pytest.raises(LookupError):
        change_hold(
            committing_database, clock, stock_operations.release_hold, ReleaseHoldRequest(hold_id="HLD-MISSING"))
