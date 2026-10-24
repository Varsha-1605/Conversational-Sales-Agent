from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select

from backend.db.schema import customers

# ARCHITECTURE 5.6, API 11, CX-05, CX-06, P-12, P-17: one gate for every use of personal data. Each caller names its
# use; which of the customer's two choices that use needs is decided here and nowhere else.
#   choice 1, personalization_allowed:   personal data may be used to tailor offers, suggestions and what an
#                                        associate sees; decliners still use the whole core product (CX-05)
#   choice 2, save_preferences_allowed: stated preferences and shopping facts may be saved long-term (CX-06)


class ConsentUse(StrEnum):
    PERSONALIZED_OFFERS = "personalized_offers"        # Loyalty and Offers: personalized and tier offers (LO-03)
    PERSONALIZED_SUGGESTIONS = "personalized_suggestions"  # Recommendation: profile and purchase history
    RECORD_VIEWED_PRODUCTS = "record_viewed_products"  # persist: journeys.viewed_products (DATA_SPEC 4.1)
    READ_MEMORY = "read_memory"                        # load_context: saved preferences and memories
    SAVE_MEMORY = "save_memory"                        # remember: saved preferences and memories
    ASSOCIATE_VIEW = "associate_view"                  # customer view: preferences shown to an associate (R-06)


_NEEDS_SAVE_CONSENT = {ConsentUse.SAVE_MEMORY}


@dataclass(frozen=True)
class Consent:
    personalization_allowed: bool
    save_preferences_allowed: bool


def is_allowed(consent: Consent, use: ConsentUse) -> bool:
    """Whether the customer's choices allow this use of their data."""
    if ConsentUse(use) in _NEEDS_SAVE_CONSENT:
        return consent.save_preferences_allowed
    return consent.personalization_allowed


def load_consent(connection, customer_id) -> Consent:
    """The customer's two choices as stored now. Raises LookupError for an unknown customer."""
    row = connection.execute(
        select(customers.c.personalization_allowed, customers.c.save_preferences_allowed)
        .where(customers.c.customer_id == customer_id)
    ).first()
    if row is None:
        raise LookupError(f"unknown customer {customer_id}")
    return Consent(*row)
