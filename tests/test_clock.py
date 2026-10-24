from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from backend.domain.clock import FixedClock, to_ist

# a time with microseconds, given in UTC, so a lost or misread offset shows up as a 5:30 difference
INSTANT = datetime(2026, 10, 24, 13, 15, 30, 123456, tzinfo=timezone.utc)


def test_clock_is_in_ist():
    assert FixedClock(INSTANT).now().utcoffset() == timedelta(hours=5, minutes=30)
    assert FixedClock(INSTANT).now() == INSTANT


def test_fixed_clock_advances():
    clock = FixedClock(INSTANT)
    clock.advance(days=31)
    assert clock.now() - INSTANT == timedelta(days=31)


def test_naive_times_are_refused():
    with pytest.raises(ValueError):
        to_ist(datetime(2026, 10, 24, 12, 0))


@pytest.mark.integration
def test_postgres_round_trip_keeps_the_instant(test_database):
    with test_database.connect() as connection:
        connection.execute(text("CREATE TEMP TABLE clock_check (at TIMESTAMPTZ)"))
        connection.execute(text("INSERT INTO clock_check VALUES (:at)"), {"at": to_ist(INSTANT)})
        back = connection.execute(text("SELECT at FROM clock_check")).scalar_one()
    assert to_ist(back) == INSTANT
    assert to_ist(back).utcoffset() == timedelta(hours=5, minutes=30)
