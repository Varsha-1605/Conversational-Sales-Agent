from typing import Literal

from pydantic import Field

from backend.domain.models.common import Model


class CartLine(Model):
    """journeys.cart[] (DATA_SPEC 4.1)."""

    sku: str
    quantity: int = Field(ge=1)


class Escalation(Model):
    """journeys.escalation (DATA_SPEC 4.1, SA-11)."""

    status: Literal["waiting", "with_associate", "resolved"]
    summary: str
    staff_id: str | None = None


# journeys.shortlist, viewed_products and declined_suggestions are lists of product IDs (list[str]);
# journeys.stated_preferences is a map of preference name to value (dict[str, str]).
