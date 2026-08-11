"""Home Assistant-independent FR24 candidate validation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from .models import FlightLeg


@dataclass(frozen=True, slots=True)
class CandidateResult:
    accepted: bool
    reason: str


def normalize_flight_number(value: Any) -> str:
    text = str(value or "").upper().replace(" ", "")
    return text[2:] if text.startswith("WN") else text


def validate_candidate(leg: FlightLeg, flight: dict[str, Any]) -> CandidateResult:
    """Require flight, airline, route, live state, and departure-date agreement."""
    if normalize_flight_number(flight.get("flight_number")) != leg.flight_number:
        return CandidateResult(False, "flight_number_mismatch")
    airline = str(flight.get("airline_iata") or "").upper()
    if airline != leg.airline:
        return CandidateResult(False, "airline_mismatch")
    if str(flight.get("airport_origin_code_iata") or "").upper() != leg.origin:
        return CandidateResult(False, "origin_mismatch")
    if str(flight.get("airport_destination_code_iata") or "").upper() != leg.destination:
        return CandidateResult(False, "destination_mismatch")
    scheduled = flight.get("time_scheduled_departure")
    if not isinstance(scheduled, (int, float)):
        return CandidateResult(False, "missing_departure_time")
    delta = abs(leg.scheduled_departure.timestamp() - float(scheduled))
    if delta > timedelta(hours=6).total_seconds():
        return CandidateResult(False, "departure_window_mismatch")
    if flight.get("tracked_type") != "live":
        return CandidateResult(False, "not_live")
    if flight.get("latitude") is None or flight.get("longitude") is None:
        return CandidateResult(False, "missing_position")
    if not any(flight.get(key) for key in ("id", "aircraft_registration", "aircraft_icao_24bit")):
        return CandidateResult(False, "missing_immutable_identifier")
    return CandidateResult(True, "accepted")
