import csv

import pytest

from backend.db.repositories import locations, staff
from backend.domain.references import UnknownReference, require_location, require_skus, require_staff
from config import PROJECT_ROOT

pytestmark = pytest.mark.integration


@pytest.fixture
def connection(sql_server):
    with sql_server.connect() as connection:
        yield connection


def test_known_store_and_location(connection):
    store = locations.locations(connection, "store")[0]
    warehouse = locations.locations(connection, "warehouse")[0]
    assert require_location(connection, store.location_id, store=True) == store
    assert require_location(connection, warehouse.location_id) == warehouse
    with pytest.raises(UnknownReference) as error:
        require_location(connection, warehouse.location_id, store=True)  # a warehouse is not a store
    assert error.value.kind == "store"


def test_unknown_location_is_refused(connection):
    with pytest.raises(UnknownReference) as error:
        require_location(connection, "LOC-NONE")
    assert error.value.ids == ("LOC-NONE",)


def test_skus_are_checked_together(connection):
    require_skus(connection, ["SKU-4001-L-IVO", "SKU-4001-L-IVO"])
    with pytest.raises(UnknownReference) as error:
        require_skus(connection, ["SKU-4001-L-IVO", "SKU-NONE-1", "SKU-NONE-2"])
    assert error.value.ids == ("SKU-NONE-1", "SKU-NONE-2")


def test_staff_and_their_store(connection):
    with open(PROJECT_ROOT / "data" / "raw" / "staff_data.csv", newline="", encoding="utf-8-sig") as file:
        row = next(row for row in csv.DictReader(file) if row["role"] == "store_associate")
    associate = staff.staff_member(connection, row["staff_id"])
    assert require_staff(connection, associate.staff_id) == associate
    assert require_staff(connection, associate.staff_id, store_id=associate.location_id) == associate
    with pytest.raises(UnknownReference):
        require_staff(connection, associate.staff_id, store_id="LOC-NONE")
    with pytest.raises(UnknownReference):
        require_staff(connection, "STF-NONE")
