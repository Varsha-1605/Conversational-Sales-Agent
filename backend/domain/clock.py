from datetime import datetime, timedelta, timezone

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
    """The same instant in IST. Raises ValueError for a naive datetime, whose instant is unknown."""
    if value.tzinfo is None:
        raise ValueError("a time without a timezone is ambiguous; every stored time carries its offset")
    return value.astimezone(IST)
