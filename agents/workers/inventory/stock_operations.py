from collections import defaultdict

from sqlalchemy import insert, select, update

from agents.workers.inventory import stock_rules
from agents.workers.inventory.contract import (
    AvailabilityReply, AvailabilityRequest, CommitHoldRequest, CreateHoldReply, CreateHoldRequest,
    FulfillmentOption, FulfillmentOptionsReply, FulfillmentOptionsRequest, HoldReply, ReleaseHoldRequest,
)
from backend.db.repositories import inventory as client_stock
from backend.db.repositories import locations as client_locations
from backend.db.schema import stock_holds
from backend.domain.database_locks import lock_stock
from backend.domain.status_changes import STOCK_HOLD_STATUS, require_hold_can_commit
from backend.domain.stock_availability import quantity_left_after_holds
from backend.domain.transaction import write_operation

# AGENTS 3.2: the only source of stock and availability. It reads the client's on-hand quantities (SQL Server,
# read only) and subtracts our holds (PostgreSQL). It never calls an LLM and never changes client data.
# `postgres` and `client` are open connections to the two databases; `now` comes from the caller's clock.

_METHOD_ORDER = ("ship_to_home", "click_collect", "in_store")


class OutOfStock(Exception):
    """Fewer units are available than were asked for. Nothing was held."""

    def __init__(self, sku, location_id, available, requested):
        self.sku = sku
        self.location_id = location_id
        self.available = available
        self.requested = requested
        super().__init__(f"{sku} at {location_id}: {available} available, {requested} requested")


class StockNotConfirmed(Exception):
    """The stock record is older than the freshness limit, so nothing may be confirmed on it (DR-08)."""

    def __init__(self, sku, location_id):
        self.sku = sku
        self.location_id = location_id
        super().__init__(f"stock of {sku} at {location_id} is not confirmed")


def _describe_stock(postgres, record, now) -> AvailabilityReply:
    available = quantity_left_after_holds(postgres, record, now)
    state = stock_rules.stock_state(available, record.last_updated_at, now)
    return AvailabilityReply(
        sku=record.sku, location_id=record.location_id, state=state,
        available_quantity=None if state == "unconfirmed" else available, stock_updated_at=record.last_updated_at)


def availability(postgres, client, request: AvailabilityRequest, now) -> AvailabilityReply:
    """Stock state and quantity left at one location. A location with no stock record has none: out of stock."""
    record = client_stock.stock(client, request.sku, request.location_id)
    if record is None:
        return AvailabilityReply(sku=request.sku, location_id=request.location_id, state="out_of_stock",
                                 available_quantity=0, stock_updated_at=None)
    return _describe_stock(postgres, record, now)


def _methods_offered_by(place):
    if place.ships_to_home:
        yield "ship_to_home"
    if place.supports_click_collect:
        yield "click_collect"
    if place.location_type == "store":
        yield "in_store"


def fulfillment_options(postgres, client, request: FulfillmentOptionsRequest, now) -> FulfillmentOptionsReply:
    """Every way to get the SKU from every location that has a stock record for it: ship to home, click & collect
    and in store. An option is available only when confirmed stock covers the quantity."""
    places = {place.location_id: place for place in client_locations.locations(client)}
    options = []
    for record in client_stock.stock_for(client, [request.sku]):
        stock = _describe_stock(postgres, record, now)
        covers_quantity = stock.available_quantity is not None and stock.available_quantity >= request.quantity
        for method in _methods_offered_by(places[record.location_id]):
            options.append(FulfillmentOption(
                method=method, location_id=record.location_id, location_name=places[record.location_id].location_name,
                state=stock.state, available_quantity=stock.available_quantity, is_available=covers_quantity))
    options.sort(key=lambda option: _METHOD_ORDER.index(option.method))
    return FulfillmentOptionsReply(sku=request.sku, options=options)


def _require_enough_stock(worker_connection, client, sku, location_id, requested, now):
    record = client_stock.stock(client, sku, location_id)
    if record is None:
        raise OutOfStock(sku, location_id, 0, requested)
    if stock_rules.is_stale(record.last_updated_at, now):
        raise StockNotConfirmed(sku, location_id)
    available = quantity_left_after_holds(worker_connection, record, now)
    if available < requested:
        raise OutOfStock(sku, location_id, available, requested)


def _hold_reply(hold) -> HoldReply:
    return HoldReply(**{name: hold[name] for name in HoldReply.model_fields})


@write_operation
def create_hold(worker_connection, client, request: CreateHoldRequest) -> CreateHoldReply:
    """Hold stock for a checkout, a reservation, an order or an exchange. Under the stock lock it confirms the
    quantity is still free, so two buyers of the last unit cannot both be held (IN-04, DR-09). A checkout hold
    lasts 15 minutes and a reservation hold until the end of the slot; order and exchange holds start committed.
    Raises OutOfStock or StockNotConfirmed, and nothing is held."""
    now = worker_connection.clock.now()
    status = stock_rules.starting_hold_status(request.reference_type)
    STOCK_HOLD_STATUS.require_starting_status(status)
    expires_at = stock_rules.hold_expires_at(request.reference_type, request.expires_at, now)

    wanted = defaultdict(int)
    for line in request.lines:
        wanted[(line.sku, line.location_id)] += line.quantity
    lock_stock(worker_connection, wanted)
    for (sku, location_id), quantity in wanted.items():
        _require_enough_stock(worker_connection, client, sku, location_id, quantity, now)

    holds = []
    for line in request.lines:
        hold = {
            "hold_id": worker_connection.next_id("hold"), "sku": line.sku, "location_id": line.location_id,
            "quantity": line.quantity, "reference_type": request.reference_type,
            "reference_id": request.reference_id, "order_line_id": line.order_line_id, "hold_status": status,
            "expires_at": expires_at,
        }
        worker_connection.execute(insert(stock_holds).values(**hold))
        holds.append(_hold_reply(hold))
    return CreateHoldReply(holds=holds)


def _lock_hold(worker_connection, hold_id):
    hold = worker_connection.execute(
        select(stock_holds).where(stock_holds.c.hold_id == hold_id).with_for_update()).mappings().first()
    if hold is None:
        raise LookupError(f"unknown hold {hold_id}")
    return dict(hold)


@write_operation
def commit_hold(worker_connection, request: CommitHoldRequest) -> HoldReply:
    """Turn a checkout hold into sold stock once payment succeeded: committed, pointed at the order and its line.
    Only an active hold that has not expired can be committed (DATA_SPEC 4.5); a lapsed one never is. Raises
    StatusChangeNotAllowed, LookupError for an unknown hold, ValueError for a hold that is not a checkout hold."""
    hold = _lock_hold(worker_connection, request.hold_id)
    if hold["reference_type"] != "checkout":
        raise ValueError(f"{request.hold_id} is a {hold['reference_type']} hold; only a checkout hold is committed")
    STOCK_HOLD_STATUS.require_allowed_change(hold["hold_status"], "committed")
    require_hold_can_commit(hold["expires_at"], worker_connection.clock.now())
    changes = {"hold_status": "committed", "reference_type": "order", "reference_id": request.order_id,
               "order_line_id": request.order_line_id, "expires_at": None}
    worker_connection.execute(update(stock_holds).where(stock_holds.c.hold_id == request.hold_id).values(**changes))
    return _hold_reply({**hold, **changes})


@write_operation
def release_hold(worker_connection, request: ReleaseHoldRequest) -> HoldReply:
    """Give a hold's stock back: an abandoned checkout, a cancelled reservation, a removed order line. A lapsed or
    already released hold cannot be released again (StatusChangeNotAllowed)."""
    hold = _lock_hold(worker_connection, request.hold_id)
    STOCK_HOLD_STATUS.require_allowed_change(hold["hold_status"], "released")
    worker_connection.execute(
        update(stock_holds).where(stock_holds.c.hold_id == request.hold_id).values(hold_status="released"))
    return _hold_reply({**hold, "hold_status": "released"})
