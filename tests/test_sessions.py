import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from backend.auth import sessions
from backend.auth.sessions import end_session, find_open_session, record_activity, start_session
from backend.db.schema import sessions as sessions_table
from backend.domain.clock import IST
from config import PROJECT_ROOT

START = datetime(2026, 10, 25, 12, 0, tzinfo=IST)


def test_idle_limits_match_business_rules():
    rule = json.loads((PROJECT_ROOT / "data" / "raw" / "business_rules.json").read_text(encoding="utf-8"))["sessions"]
    assert sessions.IDLE_LIMIT == timedelta(minutes=rule["session_inactivity_minutes"])
    assert sessions.KIOSK_IDLE_LIMIT == timedelta(minutes=rule["kiosk_inactivity_minutes"])


@pytest.mark.parametrize("channel, limit", [
    ("web", timedelta(minutes=30)), ("app", timedelta(minutes=30)), ("whatsapp", timedelta(minutes=30)),
    ("kiosk", timedelta(minutes=2)),
])
def test_each_channel_has_its_idle_limit(channel, limit):
    assert sessions.idle_limit(channel) == limit


@pytest.mark.integration
def test_a_new_session_is_found_with_who_it_belongs_to(rolled_back_connection):
    session = start_session(rolled_back_connection, START, "STF-011", "store_associate", "LOC-S01", "web")
    assert find_open_session(rolled_back_connection, START, session.session_id) == session
    assert len(session.session_id) == 32  # a random 128-bit ID, not a counter


@pytest.mark.integration
def test_two_sessions_never_share_an_id(rolled_back_connection):
    first = start_session(rolled_back_connection, START, "CUST-0001", "customer", None, "web")
    second = start_session(rolled_back_connection, START, "CUST-0001", "customer", None, "web")
    assert first.session_id != second.session_id


@pytest.mark.integration
def test_an_unknown_session_is_not_found(rolled_back_connection):
    assert find_open_session(rolled_back_connection, START, "no-such-session") is None


@pytest.mark.integration
@pytest.mark.parametrize("channel, idle, is_open", [
    ("web", timedelta(minutes=29, seconds=59), True),
    ("web", timedelta(minutes=30), False),
    ("kiosk", timedelta(minutes=1, seconds=59), True),
    ("kiosk", timedelta(minutes=2), False),
])
def test_a_session_idle_for_its_limit_is_no_longer_open(rolled_back_connection, channel, idle, is_open):
    session = start_session(rolled_back_connection, START, "CUST-0001", "customer", None, channel)
    found = find_open_session(rolled_back_connection, START + idle, session.session_id)
    assert (found is not None) == is_open


@pytest.mark.integration
def test_activity_slides_the_idle_limit(rolled_back_connection):
    session = start_session(rolled_back_connection, START, "CUST-0001", "customer", None, "web")
    record_activity(rolled_back_connection, START + timedelta(minutes=20), session.session_id)
    assert find_open_session(rolled_back_connection, START + timedelta(minutes=49), session.session_id)
    assert find_open_session(rolled_back_connection, START + timedelta(minutes=50), session.session_id) is None


@pytest.mark.integration
def test_signing_out_ends_the_session_at_once(rolled_back_connection):
    session = start_session(rolled_back_connection, START, "CUST-0001", "customer", None, "web")
    end_session(rolled_back_connection, START + timedelta(minutes=1), session.session_id)
    assert find_open_session(rolled_back_connection, START + timedelta(minutes=1), session.session_id) is None


@pytest.mark.integration
def test_ending_a_session_twice_keeps_the_first_end_time(rolled_back_connection):
    session = start_session(rolled_back_connection, START, "CUST-0001", "customer", None, "web")
    end_session(rolled_back_connection, START + timedelta(minutes=1), session.session_id)
    end_session(rolled_back_connection, START + timedelta(minutes=9), session.session_id)
    ended_at = rolled_back_connection.execute(select(sessions_table.c.ended_at).where(
        sessions_table.c.session_id == session.session_id)).scalar_one()
    assert ended_at == START + timedelta(minutes=1)
