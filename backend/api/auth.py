import logging
import re
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import AfterValidator, Field, StringConstraints
from sqlalchemy.exc import IntegrityError

from backend.api.dependencies import channel_if_sent, channel_or_web
from backend.api.errors import ApiError, ErrorCode, field_error
from backend.api.models import ApiModel
from backend.auth.account_lookup import email_is_taken, find_account, first_name_of, normalize_email
from backend.auth.passwords import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH, hash_password, verify_password
from backend.api.route_access import current_session
from backend.auth.session_cookie import clear_session_cookie, set_session_cookie, sign_session_cookie
from backend.auth.sessions import Session, end_session, start_session
from backend.auth.rate_limit import RateLimiter
from backend.db.connect_sqlserver import client_connection
from backend.db.repositories import locations
from backend.domain.active_channel import clear_active_channel
from backend.domain.customer_accounts import create_customer_account
from backend.domain.personalization import is_personalization_pending
from backend.domain.transaction import open_transaction
from backend.logging_setup import mask_email

# API 3.1: sign-in, sign-up, sign-out and "who am I". Passwords and request bodies are never logged (NF-04).

logger = logging.getLogger(__name__)
router = APIRouter(tags=["authentication"])

Role = Literal["customer", "store_associate", "administrator"]
ROLE_LABELS = {"customer": "customer", "store_associate": "store associate", "administrator": "administrator"}
# the page each role starts on (design page 2); the pages are named here and built in later phases
LANDING = {"customer": "home", "store_associate": "associate_home", "administrator": "integration_status"}
EMAIL_SHAPE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def landing_for(postgres, role: str, subject_id: str) -> str:
    """The page to open after sign-in. A customer who has not yet chosen a preferred store starts on Personalization,
    so a new customer sees it once, after their first sign-in (design page 3)."""
    if role == "customer" and is_personalization_pending(postgres, subject_id):
        return "personalization"
    return LANDING[role]


def _check_email_shape(email: str) -> str:
    if not EMAIL_SHAPE.fullmatch(email):
        raise ValueError("Enter an email like name@example.com")
    return email


Email = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=200),
                  AfterValidator(_check_email_shape)]
FullName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class SignInRequest(ApiModel):
    email: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)
    role: Role = "customer"
    store_id: str | None = None  # a kiosk's own store, sent by the kiosk only (P-19)


class SignInReply(ApiModel):
    subject_id: str
    role: Role
    store_id: str | None
    landing: str


class SignUpRequest(ApiModel):
    full_name: FullName
    email: Email
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)


class SignUpReply(ApiModel):
    customer_id: str


class MeReply(ApiModel):
    subject_id: str
    role: Role
    store_id: str | None
    first_name: str
    landing: str


def _email_taken_error() -> ApiError:
    return field_error("email", "This email already has an account.", "Sign in, or use a different email.")


def _start_signed_in_session(request: Request, response: Response, subject_id, role, store_id, channel) -> Session:
    """Record the session and put its cookie on the response."""
    state = request.app.state
    with open_transaction(state.postgres, state.clock) as transaction:
        session = start_session(transaction.connection, state.clock.now(), subject_id, role, store_id, channel)
    key = state.settings.session_cookie_key.get_secret_value()
    set_session_cookie(response, sign_session_cookie(key, session, state.clock))
    return session


def _client_address(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _require_not_limited(limiter: RateLimiter, key):
    """Refuse with RATE_LIMITED and Retry-After when the key has used up its allowance (API 9)."""
    wait = limiter.seconds_until_allowed(key)
    if wait:
        raise ApiError(ErrorCode.RATE_LIMITED, headers={"Retry-After": str(wait)})


def _kiosk_store(sqlserver_engine, store_id: str | None, channel: str) -> str | None:
    """The store a kiosk is set up for. Only a kiosk may send one, and it must be one of our stores."""
    if store_id is None:
        return None
    if channel != "kiosk":
        raise field_error("store_id", "Only a kiosk sets a store.", "Leave store_id out.")
    with client_connection(sqlserver_engine) as sqlserver:
        place = locations.location(sqlserver, store_id)
    if place is None or place.location_type != "store":
        raise field_error("store_id", "That is not one of our stores.", "Use the store ID this kiosk is set up for.")
    return store_id


@router.post("/auth/sign-in")
def sign_in(body: SignInRequest, request: Request, response: Response,
            channel: Annotated[str, Depends(channel_or_web)]) -> SignInReply:
    """Rate limit, check the password, then the chosen role, then start the session. A wrong role counts as a failed
    attempt and is only revealed after the password was right, so nobody can learn which emails exist (API 9)."""
    state = request.app.state
    email = normalize_email(body.email)
    address = _client_address(request)
    attempt = (address, email)
    # one address cannot try endless different emails, and one email cannot be tried endlessly from one address
    _require_not_limited(state.sign_in_address_limiter, address)
    _require_not_limited(state.sign_in_limiter, attempt)

    with state.postgres.connect() as postgres:
        kiosk_store = _kiosk_store(state.sqlserver, body.store_id, channel)
        account = find_account(postgres, state.sqlserver, email)
    if not verify_password(body.password, account.password_hash if account else None):
        state.sign_in_limiter.record(attempt)
        state.sign_in_address_limiter.record(address)
        logger.warning("sign-in refused for %s: wrong email or password", mask_email(email))
        raise ApiError(ErrorCode.AUTH_FAILED)
    if account.role != body.role:
        state.sign_in_limiter.record(attempt)
        state.sign_in_address_limiter.record(address)
        logger.warning("sign-in refused for %s: wrong role", mask_email(email))
        raise ApiError(ErrorCode.ROLE_MISMATCH, f"This account is not registered as {ROLE_LABELS[body.role]}. "
                                                "Check the role you selected and try again.")

    state.sign_in_limiter.forget(attempt)
    session = _start_signed_in_session(
        request, response, account.subject_id, account.role, account.store_id or kiosk_store, channel)
    logger.info("signed in: %s as %s", session.subject_id, session.role)
    with state.postgres.connect() as postgres:
        landing = landing_for(postgres, session.role, session.subject_id)
    return SignInReply(subject_id=session.subject_id, role=session.role, store_id=session.store_id, landing=landing)


@router.post("/auth/sign-up", status_code=201)
def sign_up(body: SignUpRequest, request: Request) -> SignUpReply:
    """A new customer account (Base tier, 0 points, device web). It does not sign the customer in: the page sends
    them to sign-in. An email that a customer or a staff member already uses is refused (AC-22)."""
    state = request.app.state
    email = normalize_email(body.email)
    address = _client_address(request)
    _require_not_limited(state.sign_up_limiter, address)
    state.sign_up_limiter.record(address)  # every attempt counts: each one costs a password hash or an email check
    with state.postgres.connect() as postgres:
        if email_is_taken(postgres, state.sqlserver, email):
            raise _email_taken_error()
    password_hash = hash_password(body.password)  # before any connection is held: a hash takes real time and memory
    try:
        with open_transaction(state.postgres, state.clock) as transaction:
            customer_id = create_customer_account(transaction, body.full_name, email, password_hash)
    except IntegrityError:  # two sign-ups with one email at the same moment: the second one lands here
        with state.postgres.connect() as postgres:
            if email_is_taken(postgres, state.sqlserver, email):
                raise _email_taken_error() from None
        raise
    logger.info("signed up: %s", customer_id)
    return SignUpReply(customer_id=customer_id)


@router.post("/auth/sign-out", status_code=204, response_class=Response)
def sign_out(request: Request, session: Annotated[Session, Depends(current_session)],
             sent_channel: Annotated[str | None, Depends(channel_if_sent)]) -> Response:
    """End the session at once. A customer's journey loses its active channel if the channel they signed out of held
    it (ARCHITECTURE 7.3)."""
    state = request.app.state
    with open_transaction(state.postgres, state.clock) as transaction:
        end_session(transaction.connection, state.clock.now(), session.session_id)
        if session.role == "customer":
            clear_active_channel(transaction, session.subject_id, sent_channel or session.channel)
    response = Response(status_code=204)
    clear_session_cookie(response)
    return response


@router.get("/me")
def me(request: Request, session: Annotated[Session, Depends(current_session)]) -> MeReply:
    state = request.app.state
    with state.postgres.connect() as postgres:
        try:
            first_name = first_name_of(postgres, state.sqlserver, session.role, session.subject_id)
        except LookupError:  # the account was removed while its session was open
            raise ApiError(ErrorCode.AUTH_REQUIRED) from None
        landing = landing_for(postgres, session.role, session.subject_id)
    return MeReply(subject_id=session.subject_id, role=session.role, store_id=session.store_id,
                   first_name=first_name, landing=landing)
