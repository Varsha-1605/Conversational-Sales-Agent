import base64
from datetime import datetime, timedelta
from decimal import Decimal
import json

import jwt
import pytest

from backend.domain.clock import IST, FixedClock
from backend.domain.signed_tokens import (
    TokenExpired, TokenRejected, TokenSigner, TokenWrongChannel, hash_quoted_state,
)

KEY = "test-signing-key-" + "k" * 32
START = datetime(2026, 10, 24, 12, 0, tzinfo=IST)
QUOTE = {"order_id": "ORD-000001", "lines": [{"line_id": "L1", "unit_price": Decimal("1999.00")}], "stock": 3}


@pytest.fixture
def clock():
    return FixedClock(START)


@pytest.fixture
def signer(clock):
    return TokenSigner(KEY, action_lifetime=timedelta(minutes=10), link_lifetime=timedelta(minutes=15), clock=clock)


def cancel_token(signer, **changes):
    fields = {"action": "cancel", "target": "ORD-000001", "customer_id": "CUST-0002", "channel": "web",
              "quoted_amounts": {"refund": Decimal("3999.00")}, "quoted_state": QUOTE, **changes}
    return signer.issue_action_token(**fields)


def test_action_token_round_trip(signer):
    claims = signer.verify_action_token(cancel_token(signer), customer_id="CUST-0002", active_channel="web")
    assert (claims.action, claims.target, claims.customer_id, claims.channel) == (
        "cancel", "ORD-000001", "CUST-0002", "web")
    assert claims.quoted_amounts == {"refund": Decimal("3999.00")}
    assert claims.expires_at == START + timedelta(minutes=10)
    assert claims.matches_state(QUOTE)


def test_each_token_has_its_own_id(signer):
    first = signer.verify_action_token(cancel_token(signer), customer_id="CUST-0002", active_channel="web")
    second = signer.verify_action_token(cancel_token(signer), customer_id="CUST-0002", active_channel="web")
    assert first.token_id != second.token_id


def test_a_changed_price_or_stock_no_longer_matches(signer):
    claims = signer.verify_action_token(cancel_token(signer), customer_id="CUST-0002", active_channel="web")
    assert not claims.matches_state({**QUOTE, "stock": 2})
    assert not claims.matches_state({**QUOTE, "lines": [{"line_id": "L1", "unit_price": Decimal("2099.00")}]})
    assert claims.matches_state(dict(reversed(list(QUOTE.items()))))  # key order does not matter


def test_expired_token_is_refused(signer, clock):
    token = cancel_token(signer)
    clock.advance(minutes=9, seconds=59)
    signer.verify_action_token(token, customer_id="CUST-0002", active_channel="web")
    clock.advance(seconds=1)
    with pytest.raises(TokenExpired):
        signer.verify_action_token(token, customer_id="CUST-0002", active_channel="web")


def test_another_customers_token_is_refused_even_when_expired(signer, clock):
    token = cancel_token(signer)
    with pytest.raises(TokenRejected):
        signer.verify_action_token(token, customer_id="CUST-0009", active_channel="web")
    clock.advance(hours=1)
    with pytest.raises(TokenRejected):
        signer.verify_action_token(token, customer_id="CUST-0009", active_channel="web")


def test_token_from_a_channel_that_is_no_longer_active_is_refused(signer):
    token = cancel_token(signer, channel="whatsapp")
    with pytest.raises(TokenWrongChannel):
        signer.verify_action_token(token, customer_id="CUST-0002", active_channel="web")


def tamper_with_claims(token, **changes):
    header, payload, signature = token.split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    claims.update(changes)
    new_payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"{header}.{new_payload}.{signature}"


@pytest.mark.parametrize("changes", [
    {"amounts": {"refund": "9999.00"}}, {"target": "ORD-000002"}, {"customer_id": "CUST-0009"},
    {"exp": 9999999999}, {"action": "return"},
])
def test_altered_token_is_refused(signer, changes):
    with pytest.raises(TokenRejected):
        signer.verify_action_token(tamper_with_claims(cancel_token(signer), **changes), customer_id="CUST-0002",
                                   active_channel="web")


def test_token_signed_with_another_key_is_refused(signer, clock):
    foreign = TokenSigner("another-key-" + "x" * 32, action_lifetime=timedelta(minutes=10),
                          link_lifetime=timedelta(minutes=15), clock=clock)
    with pytest.raises(TokenRejected):
        signer.verify_action_token(cancel_token(foreign), customer_id="CUST-0002", active_channel="web")


def test_unsigned_token_is_refused(signer):
    unsigned = jwt.encode({"kind": "action", "jti": "x", "iat": 0, "exp": 9999999999, "customer_id": "CUST-0002",
                           "channel": "web"}, None, algorithm="none")
    with pytest.raises(TokenRejected):
        signer.verify_action_token(unsigned, customer_id="CUST-0002", active_channel="web")


@pytest.mark.parametrize("garbage", ["", "not-a-token", "a.b.c"])
def test_garbage_is_refused(signer, garbage):
    with pytest.raises(TokenRejected):
        signer.verify_action_token(garbage, customer_id="CUST-0002", active_channel="web")


def test_link_token_round_trip(signer):
    token = signer.issue_link_token(target="checkout", customer_id="CUST-0002", channel="whatsapp")
    claims = signer.verify_link_token(token, customer_id="CUST-0002")
    assert (claims.target, claims.customer_id, claims.channel) == ("checkout", "CUST-0002", "whatsapp")
    assert claims.expires_at == START + timedelta(minutes=15)


def test_link_token_for_another_customer_or_expired_is_refused(signer, clock):
    token = signer.issue_link_token(target="checkout", customer_id="CUST-0002", channel="whatsapp")
    with pytest.raises(TokenRejected):
        signer.verify_link_token(token, customer_id="CUST-0009")
    clock.advance(minutes=15)
    with pytest.raises(TokenExpired):
        signer.verify_link_token(token, customer_id="CUST-0002")


def test_one_kind_cannot_be_used_as_the_other(signer):
    link = signer.issue_link_token(target="ORD-000001", customer_id="CUST-0002", channel="web")
    with pytest.raises(TokenRejected):
        signer.verify_action_token(link, customer_id="CUST-0002", active_channel="web")
    with pytest.raises(TokenRejected):
        signer.verify_link_token(cancel_token(signer), customer_id="CUST-0002")


def test_quoted_state_hash_treats_equal_amounts_alike_and_refuses_floats():
    assert hash_quoted_state({"total": Decimal("1999.0")}) == hash_quoted_state({"total": Decimal("1999.00")})
    assert hash_quoted_state({"total": Decimal("1999.00")}) != hash_quoted_state({"total": Decimal("1999.01")})
    with pytest.raises(TypeError):
        hash_quoted_state({"lines": [{"total": 1999.0}]})
    with pytest.raises(TypeError):
        hash_quoted_state({"at": object()})


def test_signer_takes_its_key_and_lifetimes_from_settings(settings):
    signer = TokenSigner.from_settings(settings, clock=FixedClock(START))
    token = signer.issue_link_token(target="checkout", customer_id="CUST-0002", channel="web")
    assert signer.verify_link_token(token, customer_id="CUST-0002").expires_at == (
        START + timedelta(minutes=settings.link_token_minutes))


def test_tokens_follow_the_domain_clock_not_the_computers_clock():
    # PyJWT must not compare the issue time with real time: a clock set ahead (tests, simulations) would be refused
    ahead = FixedClock(datetime.now(IST) + timedelta(days=3))
    signer = TokenSigner(KEY, action_lifetime=timedelta(minutes=10), link_lifetime=timedelta(minutes=15), clock=ahead)
    claims = signer.verify_action_token(cancel_token(signer), customer_id="CUST-0002", active_channel="web")
    assert claims.action == "cancel"
    behind = FixedClock(datetime.now(IST) - timedelta(days=3))
    signer = TokenSigner(KEY, action_lifetime=timedelta(minutes=10), link_lifetime=timedelta(minutes=15), clock=behind)
    assert signer.verify_link_token(signer.issue_link_token(target="checkout", customer_id="CUST-0002", channel="web"),
                                    customer_id="CUST-0002").target == "checkout"
