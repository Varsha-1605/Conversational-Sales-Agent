from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class ApiModel(BaseModel):
    """Base for every request and response body: an unknown field is refused, not ignored (API 1)."""

    model_config = ConfigDict(extra="forbid")


class Page(ApiModel, Generic[T]):
    """The list response shape (API 6)."""

    items: list[T]
    page: int
    page_size: int
    total: int
