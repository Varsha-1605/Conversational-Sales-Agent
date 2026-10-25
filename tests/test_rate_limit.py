from datetime import datetime, timedelta

from backend.auth import rate_limit
from backend.auth.rate_limit import RateLimiter
from backend.domain.clock import IST, FixedClock

WINDOW = timedelta(minutes=15)


def limiter_and_clock(limit=5):
    clock = FixedClock(datetime(2026, 10, 25, 12, 0, tzinfo=IST))
    return RateLimiter(limit, WINDOW, clock), clock


def test_the_prototype_limits_are_the_documented_ones():
    assert (rate_limit.SIGN_IN_FAILURES, rate_limit.SIGN_IN_WINDOW) == (5, timedelta(minutes=15))
    assert (rate_limit.REQUESTS_PER_SESSION, rate_limit.REQUEST_WINDOW) == (120, timedelta(minutes=1))


def test_a_key_is_allowed_until_it_reaches_the_limit():
    limiter, _ = limiter_and_clock()
    for _ in range(5):
        assert limiter.seconds_until_allowed("a") == 0
        limiter.record("a")
    assert limiter.seconds_until_allowed("a") > 0


def test_it_says_how_long_until_the_oldest_event_leaves_the_window():
    limiter, clock = limiter_and_clock()
    for _ in range(5):
        limiter.record("a")
    clock.advance(minutes=10)
    assert limiter.seconds_until_allowed("a") == 5 * 60
    clock.advance(minutes=5)
    assert limiter.seconds_until_allowed("a") == 0


def test_the_window_slides_one_event_at_a_time():
    limiter, clock = limiter_and_clock(limit=2)
    limiter.record("a")
    clock.advance(minutes=10)
    limiter.record("a")
    assert limiter.seconds_until_allowed("a") == 5 * 60  # the first leaves in 5 minutes, then one place is free
    clock.advance(minutes=5)
    assert limiter.seconds_until_allowed("a") == 0


def test_keys_are_counted_separately():
    limiter, _ = limiter_and_clock(limit=1)
    limiter.record(("203.0.113.5", "a@example.com"))
    assert limiter.seconds_until_allowed(("203.0.113.5", "a@example.com")) > 0
    assert limiter.seconds_until_allowed(("203.0.113.5", "b@example.com")) == 0
    assert limiter.seconds_until_allowed(("198.51.100.7", "a@example.com")) == 0


def test_forgetting_a_key_clears_its_count():
    limiter, _ = limiter_and_clock(limit=1)
    limiter.record("a")
    limiter.forget("a")
    limiter.forget("never-seen")
    assert limiter.seconds_until_allowed("a") == 0


def test_checking_a_key_does_not_remember_it():
    limiter, _ = limiter_and_clock()
    for number in range(100):
        limiter.seconds_until_allowed(f"nobody-{number}")
    assert limiter._events == {}


def test_expired_keys_are_swept_once_there_are_many(monkeypatch):
    monkeypatch.setattr(rate_limit, "_SWEEP_ABOVE", 3)
    limiter, clock = limiter_and_clock()
    for number in range(3):
        limiter.record(f"old-{number}")
    clock.advance(minutes=16)
    limiter.record("new")
    assert set(limiter._events) == {"new"}
