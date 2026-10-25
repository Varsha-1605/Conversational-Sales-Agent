from dataclasses import dataclass

from sqlalchemy import select, update

from backend.db.schema import customers

# Design page 3, API 3.2, CX-01, CX-05, CX-06, AC-24: what a customer tells us about themselves and how we may use
# it. These are columns of the customers row, changed directly (ARCHITECTURE 7.1). Consent is read through
# backend/domain/consent.py, which stays the one gate for using any of it.

# The page offers woman, man or other, or nothing ("Prefer not to say", stored as empty). The seed stores female and
# male, so the stored words stay and the page words map onto them.
_STORED_FROM_OPTION = {"woman": "female", "man": "male", "other": "other"}
_OPTION_FROM_STORED = {stored: option for option, stored in _STORED_FROM_OPTION.items()}

# A 12: when a customer has not chosen a store, an escalation goes to the kiosk's store, then to this one.
# The prototype's default is the first store (Indiranagar); the client names the real one.
DEFAULT_ESCALATION_STORE_ID = "LOC-S01"


@dataclass(frozen=True)
class PersonalizationChoices:
    full_name: str
    gender: str | None  # woman, man or other; None for "Prefer not to say"
    preferred_store_id: str | None
    personalization_allowed: bool  # choice 1: use my history, activity and details for suggestions
    save_preferences_allowed: bool  # choice 2: save the preferences I mention in chat to my profile


def gender_option(stored: str | None) -> str | None:
    """The page's option for a stored gender; any value the page does not offer shows as other, empty as None."""
    if not stored:
        return None
    return _OPTION_FROM_STORED.get(stored, "other")


def load_personalization(connection, customer_id: str) -> PersonalizationChoices:
    """The customer's choices as stored now. Raises LookupError for an unknown customer."""
    row = connection.execute(select(
        customers.c.full_name, customers.c.gender, customers.c.preferred_store_id,
        customers.c.personalization_allowed, customers.c.save_preferences_allowed,
    ).where(customers.c.customer_id == customer_id)).first()
    if row is None:
        raise LookupError(f"unknown customer {customer_id}")
    return PersonalizationChoices(row.full_name, gender_option(row.gender), row.preferred_store_id,
                                  row.personalization_allowed, row.save_preferences_allowed)


def is_personalization_pending(connection, customer_id: str) -> bool:
    """Whether the customer has still to choose their preferred store, which Personalization requires. Such a
    customer lands on Personalization at sign-in, so a new customer sees it once, after their first sign-in."""
    chosen = connection.execute(select(customers.c.preferred_store_id).where(
        customers.c.customer_id == customer_id)).scalar_one_or_none()
    return chosen is None


def save_personalization(transaction, customer_id: str, choices: PersonalizationChoices):
    """Replace the customer's choices inside the caller's transaction. With save consent off, saved preferences are
    deleted in the same step, so they are always empty when consent is false (AC-24, DATA_SPEC 4.8). The caller has
    already checked that preferred_store_id is one of our stores."""
    values = {
        "full_name": choices.full_name,
        "gender": _STORED_FROM_OPTION[choices.gender] if choices.gender else None,
        "preferred_store_id": choices.preferred_store_id,
        "personalization_allowed": choices.personalization_allowed,
        "save_preferences_allowed": choices.save_preferences_allowed,
    }
    if not choices.save_preferences_allowed:
        values["saved_preferences"] = None
        # Phase 12 adds the customer's long-term memories to this same step
    result = transaction.connection.execute(update(customers).where(
        customers.c.customer_id == customer_id).values(**values))
    if result.rowcount != 1:
        raise LookupError(f"unknown customer {customer_id}")


def escalation_store_id(preferred_store_id: str | None, kiosk_store_id: str | None) -> str:
    """The store whose associates receive an escalation: the customer's preferred store, else the kiosk's store,
    else the default store (A 12)."""
    return preferred_store_id or kiosk_store_id or DEFAULT_ESCALATION_STORE_ID


def effective_device_preference(stored: str) -> str:
    """The channel for a customer's preferred device. The prototype has no voice view, so voice means web (A 12)."""
    return "web" if stored == "voice" else stored
