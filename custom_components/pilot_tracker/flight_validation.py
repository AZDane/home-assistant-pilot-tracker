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
    for prefix in ("SWA", "WN"):
        if text.startswith(prefix):
            return text[len(prefix):]
    return text


def normalize_airline(flight: dict[str, Any]) -> str:
    """Normalize IATA/ICAO/name variants exposed by FlightRadar24."""
    explicit = []
    for field in ("airline_iata", "airline_icao", "airline_name"):
        token = str(flight.get(field) or "").upper().replace(" ", "")
        if not token:
            continue
        southwest = token in ("WN", "SWA") or (field == "airline_name" and token.startswith("SOUTHWEST"))
        if not southwest:
            return token
        explicit.append(token)
    if explicit:
        return "WN"
    # Infer the airline from flight identifiers only when airline data is absent.
    tokens = [str(flight.get(field) or "").upper().replace(" ", "") for field in ("flight_number", "callsign")]
    if any(token.startswith(("WN", "SWA")) for token in tokens):
        return "WN"
    return ""


def route_code(flight: dict[str, Any], endpoint: str) -> str:
    """Return the best IATA/ICAO route code supplied for one endpoint."""
    return str(
        flight.get(f"airport_{endpoint}_code_iata")
        or flight.get(f"airport_{endpoint}_code_icao")
        or ""
    ).upper()


def route_matches(reported: str, expected_iata: str) -> bool:
    """Match an IATA code or a four-letter ICAO code ending in that IATA code."""
    return reported == expected_iata or (len(reported) == 4 and reported.endswith(expected_iata))


def validate_candidate(leg: FlightLeg, flight: dict[str, Any]) -> CandidateResult:
    """Require flight, airline, route, live state, and departure-date agreement."""
    if normalize_flight_number(flight.get("flight_number")) != leg.flight_number:
        return CandidateResult(False, "flight_number_mismatch")
    airline = normalize_airline(flight)
    if airline != leg.airline:
        return CandidateResult(False, "airline_mismatch")
    origin = route_code(flight, "origin")
    if not origin:
        return CandidateResult(False, "missing_origin_data")
    if not route_matches(origin, leg.origin) and leg.qualifier != "DV-CONT":
        return CandidateResult(False, "origin_mismatch")
    destination = route_code(flight, "destination")
    if not destination:
        return CandidateResult(False, "missing_destination_data")
    if not route_matches(destination, leg.destination) and leg.qualifier != "DV":
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
