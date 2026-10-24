from datetime import datetime

import pytest
from pydantic import ValidationError

from agents.workers.inventory.contract import CommitHoldRequest, CreateHoldRequest, FulfillmentOptionsRequest
from backend.domain.clock import IST

SLOT_END = datetime(2026, 10, 25, 11, 30, tzinfo=IST)
LINE = {"sku": "SKU-1001-M-WHI", "location_id": "LOC-W01", "quantity": 1}


def test_only_a_reservation_hold_request_carries_an_expiry():
    CreateHoldRequest(reference_type="reservation", reference_id="RSV-1", lines=[LINE], expires_at=SLOT_END)
    CreateHoldRequest(reference_type="checkout", reference_id="CHK-1", lines=[LINE])
    CreateHoldRequest(reference_type="exchange", reference_id="RET-1", lines=[{**LINE, "order_line_id": "L1"}])
    with pytest.raises(ValidationError):
        CreateHoldRequest(reference_type="reservation", reference_id="RSV-1", lines=[LINE])
    with pytest.raises(ValidationError):
        CreateHoldRequest(reference_type="checkout", reference_id="CHK-1", lines=[LINE], expires_at=SLOT_END)


@pytest.mark.parametrize("changes", [
    {"lines": []},
    {"lines": [{**LINE, "quantity": 0}]},
    {"lines": [{**LINE, "colour": "navy"}]},
    {"expires_at": datetime(2026, 10, 25, 11, 30)},  # a time without a timezone
    {"reference_type": "basket"},
])
def test_a_hold_request_with_bad_lines_or_extra_fields_is_refused(changes):
    fields = {"reference_type": "reservation", "reference_id": "RSV-1", "lines": [LINE], "expires_at": SLOT_END}
    with pytest.raises(ValidationError):
        CreateHoldRequest(**{**fields, **changes})


def test_a_commit_request_needs_the_order_and_its_line():
    with pytest.raises(ValidationError):
        CommitHoldRequest(hold_id="HLD-1", order_id="ORD-1")


def test_a_fulfilment_request_needs_at_least_one_unit():
    with pytest.raises(ValidationError):
        FulfillmentOptionsRequest(sku="SKU-1001-M-WHI", quantity=0)
