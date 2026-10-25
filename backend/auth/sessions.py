from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import insert, select, update

from backend.db.schema import sessions

# API 2, ARCHITECTURE 5.1, DB-42: a session is one row. The cookie names it; the row says whose it is, which channel
# it was signed in from and when it was last used. Writes take a connection inside the caller's transaction
# (backend/domain/transaction.py), so this module never opens or commits one.

# prototype values from data/raw/business_rules.json "sessions" (P-10);
# tests/test_session_rules_match_business_rules.py fails if they drift from the file
IDLE_LIMIT = timedelta(minutes=30)
KIOSK_IDLE_LIMIT = timedelta(minutes=2)


@dataclass(frozen=True)
class Session:
    session_id: str
    subject_id: str  # a customer_id or a staff_id
    role: str
    store_id: str | None  # an associate's store, or the store a kiosk is set up for
    channel: str  # the channel it was signed in from


def idle_limit(channel: str) -> timedelta:
    return KIOSK_IDLE_LIMIT if channel == "kiosk" else IDLE_LIMIT


def start_session(connection, now: datetime, subject_id, role, store_id, channel) -> Session:
    """Record a new session. Its ID is random and unguessable; the cookie that names it is also signed."""
    session = Session(session_id=uuid4().hex, subject_id=subject_id, role=role, store_id=store_id, channel=channel)
    connection.execute(insert(sessions).values(
        session_id=session.session_id, subject_id=subject_id, role=role, store_id=store_id, channel=channel,
        created_at=now, last_activity_at=now))
    return session


def find_open_session(connection, now: datetime, session_id: str) -> Session | None:
    """The session if it has not been signed out and has not been idle for its limit (IDLE_LIMIT, or
    KIOSK_IDLE_LIMIT on a kiosk); otherwise None. Reads only."""
    row = connection.execute(select(sessions).where(sessions.c.session_id == session_id)).mappings().first()
    if row is None or row["ended_at"] is not None or now - row["last_activity_at"] >= idle_limit(row["channel"]):
        return None
    return Session(session_id=row["session_id"], subject_id=row["subject_id"], role=row["role"],
                   store_id=row["store_id"], channel=row["channel"])


def record_activity(connection, now: datetime, session_id: str):
    """Slide the idle limit: the session was used just now."""
    connection.execute(update(sessions).where(sessions.c.session_id == session_id).values(last_activity_at=now))


def end_session(connection, now: datetime, session_id: str):
    """Sign out: the session can no longer be used, even with a valid cookie."""
    connection.execute(update(sessions).where(
        sessions.c.session_id == session_id, sessions.c.ended_at.is_(None)).values(ended_at=now))
