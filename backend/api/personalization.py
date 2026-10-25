import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import Field, StringConstraints

from backend.api.errors import ApiError, ErrorCode, field_error
from backend.api.models import ApiModel
from backend.api.route_access import require_role
from backend.auth.sessions import Session
from backend.db.connect_sqlserver import client_connection
from backend.db.repositories import locations
from backend.domain.personalization import PersonalizationChoices, load_personalization, save_personalization
from backend.domain.references import UnknownReference, require_location
from backend.domain.transaction import open_transaction

# API 3.2, design page 3: a customer reads and changes their own name, gender, preferred store and the two consent
# choices. The customer is always the session's: no customer_id is taken from the request (CH-14).

logger = logging.getLogger(__name__)
router = APIRouter(tags=["personalization"])

CustomerSession = Annotated[Session, Depends(require_role("customer"))]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class StoreChoice(ApiModel):
    store_id: str
    store_name: str


class PersonalizationRequest(ApiModel):
    name: Name
    gender: Literal["woman", "man", "other"] | None  # null is "Prefer not to say"
    preferred_store_id: str = Field(min_length=1, max_length=20)  # required (A 12)
    personalization_allowed: bool
    save_preferences_allowed: bool


class PersonalizationReply(ApiModel):
    name: str
    gender: Literal["woman", "man", "other"] | None
    preferred_store_id: str | None
    personalization_allowed: bool
    save_preferences_allowed: bool
    stores: list[StoreChoice]  # the stores the page offers for the preferred-store choice


def _reply(choices: PersonalizationChoices, stores) -> PersonalizationReply:
    return PersonalizationReply(
        name=choices.full_name, gender=choices.gender, preferred_store_id=choices.preferred_store_id,
        personalization_allowed=choices.personalization_allowed,
        save_preferences_allowed=choices.save_preferences_allowed,
        stores=[StoreChoice(store_id=store.location_id, store_name=store.location_name) for store in stores])


@router.get("/personalization")
def read_personalization(request: Request, session: CustomerSession) -> PersonalizationReply:
    state = request.app.state
    with state.postgres.connect() as postgres:
        try:
            choices = load_personalization(postgres, session.subject_id)
        except LookupError:  # the account was removed while its session was open
            raise ApiError(ErrorCode.AUTH_REQUIRED) from None
    with client_connection(state.sqlserver) as sqlserver:
        stores = locations.locations(sqlserver, "store")
    return _reply(choices, stores)


@router.put("/personalization")
def save_personalization_choices(
        body: PersonalizationRequest, request: Request, session: CustomerSession) -> PersonalizationReply:
    """Replace the customer's choices. Turning save consent off deletes their saved preferences in the same step
    (AC-24). Nothing is saved if the preferred store is not one of our stores."""
    state = request.app.state
    with client_connection(state.sqlserver) as sqlserver:
        try:
            require_location(sqlserver, body.preferred_store_id, store=True)
        except UnknownReference:
            raise field_error("preferred_store_id", "That is not one of our stores.",
                              "Choose a store from the list.") from None
        stores = locations.locations(sqlserver, "store")
    choices = PersonalizationChoices(
        full_name=body.name, gender=body.gender, preferred_store_id=body.preferred_store_id,
        personalization_allowed=body.personalization_allowed,
        save_preferences_allowed=body.save_preferences_allowed)
    try:
        with open_transaction(state.postgres, state.clock) as transaction:
            save_personalization(transaction, session.subject_id, choices)
    except LookupError:
        raise ApiError(ErrorCode.AUTH_REQUIRED) from None
    logger.info("personalization saved: %s", session.subject_id)  # IDs only: no choices, no name
    return _reply(choices, stores)
