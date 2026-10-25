import csv
from datetime import datetime, time, timedelta
from decimal import Decimal

import pytest

from backend.auth.passwords import verify_password
from backend.db.repositories import catalogue, inventory, locations, staff
from backend.domain.clock import IST
from config import PROJECT_ROOT

pytestmark = pytest.mark.integration


def client_rows(name):
    # expectations come from the files the client data was loaded from
    with open(PROJECT_ROOT / "data" / "raw" / f"{name}.csv", newline="", encoding="utf-8-sig") as file:
        return list(csv.DictReader(file))


@pytest.fixture
def connection(sql_server):
    with sql_server.connect() as connection:
        yield connection


def test_product_with_its_variants(connection):
    expected = client_rows("products")[0]
    variant_rows = [row for row in client_rows("product_variants") if row["product_id"] == expected["product_id"]]

    found = catalogue.product(connection, expected["product_id"])

    assert found.product_name == expected["product_name"]
    assert found.season_tags == tuple(expected["season_tags"].split("|"))
    assert {variant.sku for variant in found.variants} == {row["sku"] for row in variant_rows}
    assert all(isinstance(variant.list_price, Decimal) for variant in found.variants)


def test_products_keep_the_order_asked_and_skip_unknown_ids(connection):
    first, second = (row["product_id"] for row in client_rows("products")[:2])
    found = catalogue.products(connection, [second, "PRD-NONE", first, second])
    assert [product.product_id for product in found] == [second, first]
    assert catalogue.product(connection, "PRD-NONE") is None
    assert catalogue.products(connection, []) == []


def test_variant_lookups(connection):
    row = client_rows("product_variants")[0]
    assert catalogue.variant(connection, row["sku"]).list_price == Decimal(row["list_price"])
    assert catalogue.variant_by_barcode(connection, row["barcode"]).sku == row["sku"]
    assert catalogue.variant_by_barcode(connection, "0000000000") is None
    assert catalogue.variant(connection, "SKU-NONE") is None


def test_recommendations_by_reason(connection):
    rows = client_rows("product_recommendations")
    product_id = rows[0]["product_id"]
    pairs = {r["recommended_product_id"] for r in rows if r["product_id"] == product_id and r["reason"] == "pairs_with"}
    every = {r["recommended_product_id"] for r in rows if r["product_id"] == product_id}

    assert {r.recommended_product_id for r in catalogue.recommendations(connection, product_id, "pairs_with")} == pairs
    assert {r.recommended_product_id for r in catalogue.recommendations(connection, product_id)} == every


def test_stock_dates_are_ist_and_the_stale_record_is_old(connection):
    record = inventory.stock(connection, "SKU-4001-L-IVO", "LOC-S03")
    assert record.last_updated_at.utcoffset() == timedelta(hours=5, minutes=30)
    assert datetime.now(IST) - record.last_updated_at > timedelta(days=30)
    assert inventory.stock(connection, "SKU-4001-L-IVO", "LOC-NONE") is None


def test_stock_for_skus(connection):
    sku = client_rows("inventory_levels")[0]["sku"]
    expected = {(r["location_id"], int(r["on_hand_qty"])) for r in client_rows("inventory_levels") if r["sku"] == sku}
    found = inventory.stock_for(connection, [sku])
    assert {(record.location_id, record.on_hand_qty) for record in found} == expected
    one_location = next(iter(expected))[0]
    assert [record.location_id for record in inventory.stock_for(connection, [sku], one_location)] == [one_location]


def test_locations_and_opening_hours(connection):
    stores = locations.locations(connection, "store")
    assert stores and all(store.location_type == "store" for store in stores)
    assert isinstance(stores[0].supports_try_on, bool)

    week = locations.opening_hours(connection, stores[0].location_id)
    assert [hours.day_of_week for hours in week] == list(locations.DAYS)
    assert (week[-1].opens_at, week[-1].closes_at) == (time(10, 0), time(17, 0))  # Sunday (DATA_SPEC 2.7)
    assert locations.location(connection, "LOC-NONE") is None


def test_staff_lookups_never_include_the_password(connection):
    row = client_rows("staff_data")[0]
    member = staff.staff_by_email(connection, row["email"].upper())  # emails compare without case
    assert member.staff_id == row["staff_id"]
    assert not hasattr(member, "password")
    assert not hasattr(member, "password_hash")
    assert row["password"] not in repr(member)
    assert staff.staff_member(connection, row["staff_id"]) == member
    assert staff.staff_member(connection, "STF-NONE") is None


def test_staff_login_gives_the_hash_that_the_demo_password_matches(connection):
    row = client_rows("staff_data")[0]
    login = staff.staff_login(connection, row["email"].upper())
    assert login.member == staff.staff_by_email(connection, row["email"])
    assert login.password_hash.startswith("$argon2id$")
    assert verify_password(row["password"], login.password_hash)
    assert not verify_password("not-the-password", login.password_hash)
    assert login.password_hash not in repr(login) and row["password"] not in repr(login)
    assert staff.staff_login(connection, "nobody@example.com") is None


@pytest.mark.parametrize("repository", [catalogue, inventory, locations, staff])
def test_repository_health(sql_server, repository):
    assert repository.health(sql_server).ok
