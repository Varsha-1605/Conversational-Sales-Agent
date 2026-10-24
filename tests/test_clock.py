from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from backend.domain.clock import IST, FixedClock, from_sql_server, to_ist, to_sql_server

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


@pytest.mark.parametrize("raw, expected", [
    ("2026-10-24 18:45:30.1234567 +05:30", datetime(2026, 10, 24, 18, 45, 30, 123456, tzinfo=IST)),
    ("2026-10-24 13:15:30.1234560 +00:00", datetime(2026, 10, 24, 18, 45, 30, 123456, tzinfo=IST)),
    ("2026-09-16 12:13:51 +05:30", datetime(2026, 9, 16, 12, 13, 51, tzinfo=IST)),
])
def test_sql_server_text_is_parsed_with_its_offset(raw, expected):
    parsed = from_sql_server(raw)
    assert parsed == expected
    assert parsed.utcoffset() == timedelta(hours=5, minutes=30)


def test_unreadable_sql_server_value_is_refused():
    with pytest.raises(ValueError):
        from_sql_server("24/10/2026 18:45")


@pytest.mark.integration
def test_postgres_round_trip_keeps_the_instant(test_database):
    with test_database.connect() as connection:
        connection.execute(text("CREATE TEMP TABLE clock_check (at TIMESTAMPTZ)"))
        connection.execute(text("INSERT INTO clock_check VALUES (:at)"), {"at": to_ist(INSTANT)})
        back = connection.execute(text("SELECT at FROM clock_check")).scalar_one()
    assert to_ist(back) == INSTANT
    assert to_ist(back).utcoffset() == timedelta(hours=5, minutes=30)


@pytest.mark.integration
def test_sql_server_round_trip_keeps_the_instant(sql_server):
    # nothing is written to the client tables: the value only passes through a CAST
    with sql_server.connect() as connection:
        back = connection.execute(text("SELECT CAST(:at AS DATETIMEOFFSET)"), {"at": to_sql_server(INSTANT)}).scalar_one()
    assert from_sql_server(back) == INSTANT


@pytest.mark.integration
def test_stock_dates_read_from_sql_server_are_ist(sql_server):
    with sql_server.connect() as connection:
        raw = connection.execute(text(
            "SELECT last_updated_at FROM dbo.inventory_levels WHERE sku = 'SKU-4001-L-IVO' AND location_id = 'LOC-S03'"
        )).scalar_one()
    stale = from_sql_server(raw)
    assert stale.utcoffset() == timedelta(hours=5, minutes=30)
    assert datetime.now(IST) - stale > timedelta(days=30)  # the one stale record stays stale
