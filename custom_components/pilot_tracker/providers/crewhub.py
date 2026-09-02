"""Parser for CrewHub calendar event descriptions."""

from __future__ import annotations

import html
import re
from collections.abc import Callable
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..airports import airport_timezone
from ..models import FlightLeg, LegStatus, Trip
from .base import ScheduleParseError

_DAY = re.compile(
    r"(?i)\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+"
    r"(?P<month>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+"
    r"(?P<day>\d{1,2})\b"
)
_FLIGHT = re.compile(
    r"(?<![\d:])(?P<flight>\d{1,4})\s+(?P<origin>[A-Z]{3})\s+"
    r"(?P<departure>\d{1,2}:\d{2})\s*(?P<departure_zone>[A-Z]{3,4})\s+"
    r"(?P<destination>[A-Z]{3})\s+(?P<arrival>\d{1,2}:\d{2})\s*"
    r"(?P<arrival_zone>[A-Z]{3,4})\b",
    re.IGNORECASE,
)
_GATE_RETURN = re.compile(
    r"(?<![\d:])(?P<flight>\d{1,4})\s+(?P<origin>[A-Z]{3})\s+"
    r"(?P<departure_zone>[A-Z]{3,4})\s+(?P<destination>[A-Z]{3})\s+"
    r"(?P<arrival_zone>[A-Z]{3,4})\b",
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
        if not re.search(r"(?i)\bLOCAL\b", cleaned):
            raise ScheduleParseError("CrewHub calendar event must identify LOCAL time")
        day_matches = list(_DAY.finditer(cleaned))
        if not day_matches:
            raise ScheduleParseError("No dated CrewHub duty periods found")

        legs: list[FlightLeg] = []
        gate_returns = 0
        previous_date: date | None = None
        for duty_period, day_match in enumerate(day_matches, 1):
            next_start = day_matches[duty_period].start() if duty_period < len(day_matches) else len(cleaned)
            service_date = self._service_date(
                _MONTHS[day_match.group("month").title()], int(day_match.group("day")),
                anchor_date, previous_date,
            )
            previous_date = service_date
            section = cleaned[day_match.end():next_start]
            movements: list[tuple[int, str, re.Match[str]]] = [
                (match.start(), "flight", match) for match in _FLIGHT.finditer(section)
            ]
            movements.extend(
                (match.start(), "gate_return", match) for match in _GATE_RETURN.finditer(section)
                if not any(full.start() <= match.start() < full.end() for full in _FLIGHT.finditer(section))
            )
            movements.sort(key=lambda item: item[0])
            parsed: list[FlightLeg | tuple[re.Match[str], int]] = []
            for position, kind, match in movements:
                values = match.groupdict()
                origin = values["origin"].upper()
                destination = values["destination"].upper()
                if kind == "gate_return":
                    if origin == destination:
                        parsed.append((match, position))
                    continue
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
                parsed.append(FlightLeg(
                    0, service_date.isoformat(), values["flight"], "WN",
                    origin, destination, departure, arrival, duty_period=duty_period,
                ))
            for index, movement in enumerate(parsed):
                if isinstance(movement, FlightLeg):
                    movement.sequence = len(legs) + 1
                    legs.append(movement)
                    continue
                match, _position = movement
                previous = next(
                    (item for item in reversed(parsed[:index]) if isinstance(item, FlightLeg)), None
                )
                following = next(
                    (item for item in parsed[index + 1:] if isinstance(item, FlightLeg)), None
                )
                airport = match.group("origin").upper()
                if (
                    previous is None or following is None
                    or previous.destination != airport or following.origin != airport
                    or following.scheduled_departure <= previous.scheduled_arrival
                ):
                    continue
                legs.append(FlightLeg(
                    len(legs) + 1, service_date.isoformat(), match.group("flight"), "WN",
                    airport, airport, previous.scheduled_arrival, following.scheduled_departure,
                    status=LegStatus.SKIPPED, duty_period=duty_period, qualifier="GR",
                ))
                gate_returns += 1
        if not legs:
            raise ScheduleParseError("No CrewHub flight legs found")

        pairing = re.search(r"(?i)\bTrip\s*:\s*([A-Z0-9-]+)", cleaned)
        trip_id = identifier or (pairing.group(1).upper() if pairing else legs[0].date)
        return Trip(
            trip_id, "crewhub_calendar", "airport_local", legs,
            metadata={
                "schedule_time_mode": "local",
                "format": "crewhub_calendar",
                "calendar_anchor_date": anchor_date.isoformat(),
                "gate_return_count": gate_returns,
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
