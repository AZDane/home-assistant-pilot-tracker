"""Schedule provider contract."""

from typing import Protocol

from ..models import Trip


class ScheduleParseError(ValueError):
    """Raised for input that is not a supported schedule."""


class ScheduleProvider(Protocol):
    def parse(self, text: str, *, year: int | None = None) -> Trip: ...
