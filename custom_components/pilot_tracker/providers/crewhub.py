"""Parser for CrewHub calendar event descriptions."""

from __future__ import annotations

import html
import re
from collections.abc import Callable
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..airports import airport_timezone
from ..models import FlightLeg, Trip
from .base import ScheduleParseError

_DAY = re.compile(
    r"(?mi)^\s*(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+"
    r"(?P<month>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+"
    r"(?P<day>\d{1,2})\s*$"
)
_FLIGHT = re.compile(
    r"^\s*(?P<flight>\d{1,4})\s+(?P<origin>[A-Z]{3})\s+"
    r"(?P<departure>\d{1,2}:\d{2})\s*(?P<departure_zone>[A-Z]{3,4})\s+"
    r"(?P<destination>[A-Z]{3})\s+(?P<arrival>\d{1,2}:\d{2})\s*"
    r"(?P<arrival_zone>[A-Z]{3,4})\s*$",
    re.IGNORECASE,
)
_MONTHS = {name: number for number, name in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1
)}


class CrewHubCalendarProvider:
    """Convert one CrewHub pairing calendar description into a Trip."""

    def __init__(self, airport_timezone_lookup: Callable[[str], str] = airport_timezone) -> None:
        self._airport_timezone = airport_timezone_lookup

    def parse(self, text: str, *, anchor_date: date, identifier: str | None = None) -> Trip:
        cleaned = html.unescape(text).replace("\xa0", " ")
        cleaned = re.sub(r"(?i)<br\s*/?>", "\n", cleaned)
        cleaned = re.sub(r"<[^>]+>", "", cleaned)
        if not re.search(r"(?mi)^\s*LOCAL\s*$", cleaned):
            raise ScheduleParseError("CrewHub calendar event must identify LOCAL time")
        day_matches = list(_DAY.finditer(cleaned))
        if not day_matches:
            raise ScheduleParseError("No dated CrewHub duty periods found")

        legs: list[FlightLeg] = []
        previous_date: date | None = None
        for duty_period, day_match in enumerate(day_matches, 1):
            next_start = day_matches[duty_period].start() if duty_period < len(day_matches) else len(cleaned)
            service_date = self._service_date(
                _MONTHS[day_match.group("month").title()], int(day_match.group("day")),
                anchor_date, previous_date,
            )
            previous_date = service_date
            section = cleaned[day_match.end():next_start]
            for line in section.splitlines():
                match = _FLIGHT.fullmatch(line)
                if not match:
                    continue
                values = match.groupdict()
                origin = values["origin"].upper()
                destination = values["destination"].upper()
                try:
                    departure_zone = ZoneInfo(self._airport_timezone(origin))
                    arrival_zone = ZoneInfo(self._airport_timezone(destination))
                except (KeyError, ValueError, ZoneInfoNotFoundError) as error:
                    raise ScheduleParseError(str(error)) from error
                departure = self._at(service_date, values["departure"], departure_zone)
                arrival = self._at(service_date, values["arrival"], arrival_zone)
                if arrival <= departure:
                    arrival += timedelta(days=1)
                self._validate_abbreviation(departure, values["departure_zone"], origin)
                self._validate_abbreviation(arrival, values["arrival_zone"], destination)
                legs.append(FlightLeg(
                    len(legs) + 1, service_date.isoformat(), values["flight"], "WN",
                    origin, destination, departure, arrival, duty_period=duty_period,
                ))
        if not legs:
            raise ScheduleParseError("No CrewHub flight legs found")

        trip_id = identifier or legs[0].date
        return Trip(
            trip_id, "crewhub_calendar", "airport_local", legs,
            metadata={
                "schedule_time_mode": "local",
                "format": "crewhub_calendar",
                "calendar_anchor_date": anchor_date.isoformat(),
            },
        )

    @staticmethod
    def _service_date(month: int, day: int, anchor: date, previous: date | None) -> date:
        candidates = [date(anchor.year + offset, month, day) for offset in (-1, 0, 1)]
        if previous is not None:
            forward = [candidate for candidate in candidates if candidate >= previous]
            if forward:
                return min(forward, key=lambda candidate: candidate - previous)
        return min(candidates, key=lambda candidate: abs(candidate - anchor))

    @staticmethod
    def _at(service_date: date, value: str, timezone: ZoneInfo) -> datetime:
        hour, minute = (int(part) for part in value.split(":"))
        return datetime(service_date.year, service_date.month, service_date.day, hour, minute, tzinfo=timezone)

    @staticmethod
    def _validate_abbreviation(value: datetime, shown: str, airport: str) -> None:
        actual = value.tzname()
        if actual and actual.upper() != shown.upper():
            raise ScheduleParseError(
                f"Time zone {shown.upper()} does not match {airport} ({actual}) on {value.date()}"
            )
