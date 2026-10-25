from dataclasses import dataclass
from typing import Annotated

from fastapi import Header, Query

from backend.api.errors import ApiError, ErrorCode

# the channels a request can come from (API 1). Deliberately narrower than backend/db/schema.py CHANNELS, which
# also allows "voice" as a stored value: voice has no view in the prototype, so no request can come from it.
CHANNELS = ("web", "app", "whatsapp", "kiosk")

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 50


def channel(x_channel: Annotated[str | None, Header()] = None) -> str:
    """The channel a journey-changing request comes from (X-Channel; API 1, 2). Routes marked Ch depend on it."""
    if x_channel not in CHANNELS:
        raise ApiError(ErrorCode.VALIDATION_ERROR, details=[{
            "field": "X-Channel",
            "problem": "missing or not a known channel",
            "fix": f"Send one of: {', '.join(CHANNELS)}.",
        }])
    return x_channel


def channel_or_web(x_channel: Annotated[str | None, Header()] = None) -> str:
    """The channel a sign-in or sign-up comes from. They do not change a journey, so the header may be left out:
    the channel is then web. A kiosk sends kiosk."""
    return channel(x_channel) if x_channel is not None else "web"


def channel_if_sent(x_channel: Annotated[str | None, Header()] = None) -> str | None:
    """The channel the request says it comes from, or None when the header is missing or not a known channel."""
    return x_channel if x_channel in CHANNELS else None


@dataclass(frozen=True)
class PageRequest:
    page: int
    page_size: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


def pagination(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> PageRequest:
    """page from 1; page_size 1 to 50, default 20 (API 6)."""
    return PageRequest(page=page, page_size=page_size)
