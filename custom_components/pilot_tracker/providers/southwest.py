"""Parser for Southwest pairing text using Herb (Central) time."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..airports import airport_timezone
from ..const import TIME_BASIS
from ..models import FlightLeg, Trip
from .base import ScheduleParseError

_LEG = re.compile(
    # Text copied from the print view uses `14 Aug`, while PDF extraction and
    # the compact print view use `14Aug`. Accept either without changing the
    # downstream Herb-time interpretation.
    r"(?mi)^\s*(?P<day>\d{1,2})\s*(?P<month>[A-Za-z]{3})\s+"
    r"(?:(?P<qualifier>DM)\s+)?"
    r"(?P<flight>\d{1,4})\s+(?P<origin>[A-Z]{3})\s+"
    r"(?P<departure>\d{4})\s+(?P<destination>[A-Z]{3})\s+(?P<arrival>\d{4})\b"
)
_LOCAL_ROSTER_LEG = re.compile(
    r"^\s*(?P<month>\d{1,2})/(?P<day>\d{1,2})/(?P<year>20\d{2})\s+"
    r"(?P<flight>\d{1,4})\s+(?P<origin>[A-Z]{3})\s+"
    r"(?P<departure>\d{4})\s+(?P<destination>[A-Z]{3})\s+(?P<arrival>\d{4})\s*$",
    re.IGNORECASE,
)
_IPHONE_SIGNATURE = re.compile(r"^\s*Sent from my iPhone\s*$", re.IGNORECASE)
_MONTHS = {name: number for number, name in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1
)}


class SouthwestPairingProvider:
    source = "southwest_pairing"
    time_basis = TIME_BASIS

    def __init__(self, airport_timezone_lookup: Callable[[str], str] = airport_timezone) -> None:
        self._airport_timezone = airport_timezone_lookup

    def parse(self, text: str, *, year: int | None = None) -> Trip:
        local_roster = self._local_roster_rows(text)
        if local_roster is not None:
            return self._parse_local_roster(local_roster)
        time_mode = self._time_mode(text)
        matches = list(_LEG.finditer(text))
        if not matches:
            raise ScheduleParseError("No Southwest pairing legs found")
        inferred_year = year or self._extract_year(text)
        if inferred_year is None:
            raise ScheduleParseError("Pairing year is required when it is not present in the text")
        domicile_airport = matches[0].group("origin")
        domicile_timezone = None
        if time_mode == "domicile":
            try:
                domicile_timezone = ZoneInfo(self._airport_timezone(domicile_airport))
            except (KeyError, ValueError, ZoneInfoNotFoundError) as error:
                raise ScheduleParseError(str(error)) from error
        legs: list[FlightLeg] = []
        for sequence, match in enumerate(matches, 1):
            values = match.groupdict()
            month = _MONTHS.get(values["month"].title())
            if month is None:
                raise ScheduleParseError(f"Unknown month: {values['month']}")
            service_date = date(inferred_year, month, int(values["day"]))
            try:
                departure_timezone, arrival_timezone = self._leg_timezones(
                    time_mode, values["origin"], values["destination"], domicile_timezone
                )
            except (KeyError, ValueError, ZoneInfoNotFoundError) as error:
                raise ScheduleParseError(str(error)) from error
            departure = self._at(service_date, values["departure"], departure_timezone)
            arrival = self._at(service_date, values["arrival"], arrival_timezone)
            if arrival <= departure:
                arrival += timedelta(days=1)
            duty_period = 1 + len(re.findall(r"(?mi)^.*\bRls\s+\d{4}\b", text[:match.start()]))
            legs.append(FlightLeg(sequence, service_date.isoformat(), values["flight"], "WN",
                                  values["origin"], values["destination"], departure, arrival,
                                  duty_period=duty_period, qualifier=values.get("qualifier")))
        if time_mode == "herb":
            time_basis = self.time_basis
        elif time_mode == "domicile":
            time_basis = domicile_timezone.key
        else:
            time_basis = "airport_local"
        return Trip(
            self._trip_id(text), self.source, time_basis, legs,
            revision_date=self._revision_date(text),
            metadata={"schedule_time_mode": time_mode, "domicile_airport": domicile_airport},
        )

    @staticmethod
    def _local_roster_rows(text: str) -> list[re.Match[str]] | None:
        """Strictly recognize rows copied from the local-time roster view."""
        rows: list[re.Match[str]] = []
        for line in text.splitlines():
            if not line.strip() or _IPHONE_SIGNATURE.fullmatch(line):
                continue
            match = _LOCAL_ROSTER_LEG.fullmatch(line)
            if match is None:
                return None
            rows.append(match)
        return rows or None

    def _parse_local_roster(self, rows: list[re.Match[str]]) -> Trip:
        legs: list[FlightLeg] = []
        duty_dates: dict[date, int] = {}
        service_dates: list[date] = []
        for sequence, match in enumerate(rows, 1):
            values = match.groupdict()
            service_date = date(int(values["year"]), int(values["month"]), int(values["day"]))
            service_dates.append(service_date)
            try:
                departure_timezone = ZoneInfo(self._airport_timezone(values["origin"].upper()))
                arrival_timezone = ZoneInfo(self._airport_timezone(values["destination"].upper()))
            except (KeyError, ValueError, ZoneInfoNotFoundError) as error:
                raise ScheduleParseError(str(error)) from error
            departure = self._at(service_date, values["departure"], departure_timezone)
            arrival = self._at(service_date, values["arrival"], arrival_timezone)
            if arrival <= departure:
                arrival += timedelta(days=1)
            duty_period = duty_dates.setdefault(service_date, len(duty_dates) + 1)
            legs.append(FlightLeg(
                sequence, service_date.isoformat(), values["flight"], "WN",
                values["origin"].upper(), values["destination"].upper(), departure, arrival,
                duty_period=duty_period,
            ))

        span = max(service_dates) - min(service_dates)
        is_month_roster = span > timedelta(days=7)
        if is_month_roster:
            month_counts: dict[tuple[int, int], int] = {}
            for service_date in service_dates:
                key = (service_date.year, service_date.month)
                month_counts[key] = month_counts.get(key, 0) + 1
            roster_year, roster_month = max(month_counts, key=lambda key: (month_counts[key], key))
            trip_id = f"{date(roster_year, roster_month, 1):%b}".upper() + f"-{roster_year}"
            identifier_basis = "month"
        else:
            trip_id = min(service_dates).isoformat()
            identifier_basis = "start_date"

        return Trip(
            trip_id, "southwest_local_roster", "airport_local", legs,
            metadata={
                "schedule_time_mode": "local",
                "format": "local_roster",
                "identifier_basis": identifier_basis,
            },
        )

    @staticmethod
    def _time_mode(text: str) -> str:
        upper = text.upper()
        for label, mode in (("DOMICILE TIME", "domicile"), ("LOCAL TIME", "local"), ("HERB TIME", "herb")):
            if label in upper:
                return mode
        raise ScheduleParseError("Southwest pairing must identify Herb Time, Local Time, or Domicile Time")

    def _leg_timezones(
        self, mode: str, origin: str, destination: str, domicile_timezone: ZoneInfo | None
    ) -> tuple[ZoneInfo, ZoneInfo]:
        if mode == "herb":
            herb = ZoneInfo(self.time_basis)
            return herb, herb
        if mode == "domicile":
            if domicile_timezone is None:
                raise ValueError(f"No time zone found for domicile airport {origin}")
            return domicile_timezone, domicile_timezone
        return (
            ZoneInfo(self._airport_timezone(origin)),
            ZoneInfo(self._airport_timezone(destination)),
        )

    @staticmethod
    def _at(day: date, hhmm: str, timezone: ZoneInfo) -> datetime:
        return datetime(day.year, day.month, day.day, int(hhmm[:2]), int(hhmm[2:]), tzinfo=timezone)

    @staticmethod
    def _extract_year(text: str) -> int | None:
        dated = re.search(r"(?i)\bdated\s+\d{1,2}[A-Za-z]{3}(\d{2})\b", text)
        if dated:
            return 2000 + int(dated.group(1))
        years = re.findall(r"\b20\d{2}\b", text)
        return int(years[0]) if years else None

    @staticmethod
    def _trip_id(text: str) -> str:
        for pattern in (r"(?mi)^\s*Trip\s+([A-Z0-9-]+)\s+(?:dated|on)\b",
                        r"(?mi)^\s*(?:PAIRING|TRIP)\s*(?:ID|NO|#|:)[:\s]*([A-Z0-9-]+)",
                        r"(?mi)^\s*([A-Z]{4})\s+(?:PAIRING|TRIP)\b"):
            match = re.search(pattern, text)
            if match:
                return match.group(1)
        return "IMPORTED"

    @staticmethod
    def _revision_date(text: str) -> str | None:
        match = re.search(r"(?i)(?:GENERATED|REVISED|REVISION)\D{0,12}(20\d{2})[-/]?(\d{2})[-/]?(\d{2})", text)
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}" if match else None
