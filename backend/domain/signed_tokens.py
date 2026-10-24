from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
import hashlib
import hmac
import json
import secrets

import jwt

from backend.domain.clock import IST, Clock
from backend.domain.money import to_money

# API 2, ARCHITECTURE 5.6 and 7.3. Two kinds of short-lived signed token:
# - an action token sits on a confirm button. It binds the action, its target, the customer, the issuing channel,
#   the quoted amounts and a hash of the quoted state. /actions/confirm verifies it, then rechecks the rules and
#   compares the state hash; a changed price or stock means a new card (LO-06).
# - a link token sits on an agent hand-off link (go_to_checkout, view_order, "Ask the agent about this"). It names
#   the issuing channel and the customer, so the page runs under that channel. It grants no permission: the viewer
#   must already be signed in as that customer.
# Both are HS256 tokens signed with TOKEN_SIGNING_KEY. Each kind is refused where the other is expected.

ALGORITHM = "HS256"
ACTION_TOKEN = "action"
LINK_TOKEN = "link"


class TokenError(Exception):
    """A token that must not be acted on. Nothing has been changed."""


class TokenRejected(TokenError):
    """Altered, signed with another key, of the wrong kind, or issued to another customer (FORBIDDEN)."""


class TokenExpired(TokenError):
    """Past its lifetime (TOKEN_EXPIRED)."""


class TokenWrongChannel(TokenError):
    """Issued on a channel that is no longer the journey's active channel (NOT_ACTIVE_CHANNEL, A 7.3)."""


def hash_quoted_state(state) -> str:
    """A stable hash of the state a proposal was quoted on (prices, stock, eligibility inputs). Key order does not
    matter; money must be a Decimal, so 1999.00 and 1999.0 are the same amount. Raises TypeError for a float or any
    other value without one exact text form."""
    canonical = json.dumps(_to_canonical_value(state), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _to_canonical_value(value):
    if isinstance(value, dict):
        return {str(key): _to_canonical_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_canonical_value(item) for item in value]
    if isinstance(value, Decimal):
        return str(to_money(value))
    if isinstance(value, datetime):
        return value.astimezone(IST).isoformat()
    if value is None or isinstance(value, (str, int)):  # bool is an int
        return value
    raise TypeError(f"{type(value).__name__} cannot be part of a quoted state")


@dataclass(frozen=True)
class ActionClaims:
    token_id: str  # also the idempotency key: a repeated confirm returns the first result
    action: str
    target: str
    customer_id: str
    channel: str
    quoted_amounts: dict[str, Decimal]
    state_hash: str
    expires_at: datetime

    def matches_state(self, current_state) -> bool:
        """True if the state rechecked at confirm is the state that was quoted."""
        return hmac.compare_digest(self.state_hash, hash_quoted_state(current_state))


@dataclass(frozen=True)
class LinkClaims:
    target: str
    customer_id: str
    channel: str  # the page runs under this channel, not the one the link was opened on
    expires_at: datetime


class TokenSigner:
    def __init__(self, key: str, *, action_lifetime: timedelta, link_lifetime: timedelta, clock: Clock | None = None):
        self._key = key
        self._lifetimes = {ACTION_TOKEN: action_lifetime, LINK_TOKEN: link_lifetime}
        self._clock = clock or Clock()

    @classmethod
    def from_settings(cls, settings, clock=None):
        return cls(settings.token_signing_key.get_secret_value(),
                   action_lifetime=timedelta(minutes=settings.action_token_minutes),
                   link_lifetime=timedelta(minutes=settings.link_token_minutes), clock=clock)

    def issue_action_token(self, *, action, target, customer_id, channel, quoted_amounts, quoted_state) -> str:
        return self._issue(ACTION_TOKEN, {
            "action": action, "target": target, "customer_id": customer_id, "channel": channel,
            "amounts": {name: str(to_money(amount)) for name, amount in quoted_amounts.items()},
            "state": hash_quoted_state(quoted_state),
        })

    def verify_action_token(self, token, *, customer_id, active_channel) -> ActionClaims:
        """The claims of a valid action token for this customer. Raises TokenRejected, TokenExpired or
        TokenWrongChannel, checked in that order, so a foreign token is never reported as merely expired."""
        claims = self._verify(token, ACTION_TOKEN, customer_id)
        if claims["channel"] != active_channel:
            raise TokenWrongChannel(f"issued on {claims['channel']}")
        return ActionClaims(
            token_id=claims["jti"], action=claims["action"], target=claims["target"],
            customer_id=claims["customer_id"], channel=claims["channel"],
            quoted_amounts={name: to_money(amount) for name, amount in claims["amounts"].items()},
            state_hash=claims["state"], expires_at=_ist_from_timestamp(claims["exp"]),
        )

    def issue_link_token(self, *, target, customer_id, channel) -> str:
        return self._issue(LINK_TOKEN, {"target": target, "customer_id": customer_id, "channel": channel})

    def verify_link_token(self, token, *, customer_id) -> LinkClaims:
        """The claims of a valid link token for the signed-in customer. Raises TokenRejected or TokenExpired."""
        claims = self._verify(token, LINK_TOKEN, customer_id)
        return LinkClaims(target=claims["target"], customer_id=claims["customer_id"], channel=claims["channel"],
                          expires_at=_ist_from_timestamp(claims["exp"]))

    def _issue(self, kind, claims) -> str:
        now = self._clock.now()
        return jwt.encode({
            **claims, "kind": kind, "jti": secrets.token_urlsafe(16),
            "iat": int(now.timestamp()), "exp": int((now + self._lifetimes[kind]).timestamp()),
        }, self._key, algorithm=ALGORITHM)

    def _verify(self, token, kind, customer_id) -> dict:
        try:
            # times are checked below against the domain clock, never PyJWT's real clock, so the two cannot disagree
            claims = jwt.decode(token, self._key, algorithms=[ALGORITHM],
                                options={"verify_exp": False, "verify_iat": False,
                                         "require": ["kind", "jti", "iat", "exp"]})
        except jwt.InvalidTokenError as error:
            raise TokenRejected("the token is not valid") from error
        if claims["kind"] != kind:
            raise TokenRejected(f"expected the {kind} kind of token")
        if not hmac.compare_digest(str(claims.get("customer_id", "")).encode(), customer_id.encode()):
            raise TokenRejected("issued to another customer")
        if self._clock.now().timestamp() >= claims["exp"]:
            raise TokenExpired("the token has expired")
        return claims


def _ist_from_timestamp(seconds) -> datetime:
    return datetime.fromtimestamp(seconds, IST)
