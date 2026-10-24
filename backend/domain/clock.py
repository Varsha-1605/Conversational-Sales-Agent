from datetime import datetime, timedelta, timezone
import re

# All times are in IST (ARCHITECTURE 7.6, DB-20). India has no daylight saving, so a fixed offset is exact.
IST = timezone(timedelta(hours=5, minutes=30), "IST")


class Clock:
    """The current time in IST. Code takes a clock instead of calling datetime.now(), so tests can fix the time."""

    def now(self) -> datetime:
        return datetime.now(IST)


class FixedClock(Clock):
    def __init__(self, at: datetime):
        self.at = to_ist(at)

    def now(self) -> datetime:
        return self.at

    def advance(self, **delta):
        self.at += timedelta(**delta)


def to_ist(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("a time without a timezone is ambiguous; every stored time carries its offset")
    return value.astimezone(IST)


# The legacy ODBC driver ({SQL Server}, DB-05) does not understand DATETIMEOFFSET: it returns the value as text,
# and a Python datetime sent to it loses its offset. So SQL Server times always cross as text, through these two.
_SQL_SERVER_TIME = re.compile(
    r"(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})(?:\.(\d{1,7}))? ([+-]\d{2}:\d{2})"
)


def from_sql_server(value) -> datetime:
    """A DATETIMEOFFSET as returned by SQL Server (text with the legacy driver) -> an aware datetime in IST."""
    if isinstance(value, datetime):
        return to_ist(value)
    match = _SQL_SERVER_TIME.fullmatch(value)
    if match is None:
        raise ValueError("not a SQL Server DATETIMEOFFSET value")
    day, clock_time, fraction, offset = match.groups()
    fraction = (fraction or "").ljust(6, "0")[:6]  # SQL Server keeps 7 digits; Python keeps 6
    return to_ist(datetime.fromisoformat(f"{day}T{clock_time}.{fraction}{offset}"))


def to_sql_server(value: datetime) -> str:
    """An aware datetime -> ISO 8601 text with its offset, for CAST(:value AS DATETIMEOFFSET) in a query."""
    return to_ist(value).isoformat(timespec="microseconds")
