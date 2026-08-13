"""Arrival evidence helpers independent of Home Assistant."""

from __future__ import annotations

from math import asin, cos, radians, sin, sqrt
from typing import Any

from .flight_validation import normalize_flight_number, route_code, route_matches
from .models import FlightLeg


def distance_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in nautical miles."""
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 3440.065 * 2 * asin(sqrt(a))


def arrival_signals(flight: dict[str, Any], lifecycle_event: dict[str, Any] | None) -> set[str]:
    signals: set[str] = set()
    if lifecycle_event:
        event_type = lifecycle_event.get("event_type", "")
        if event_type.endswith("_arrived_gate"):
            signals.add("gate_event")
        elif event_type.endswith("_landed"):
            signals.add("landed_event")
    coordinates = (
        flight.get("latitude"), flight.get("longitude"),
        flight.get("airport_destination_latitude"), flight.get("airport_destination_longitude"),
    )
    if all(isinstance(value, (int, float)) for value in coordinates):
        proximity = distance_nm(*coordinates)
        if proximity <= 15:
            signals.add("destination_near")
        if proximity <= 5 and (
            flight.get("on_ground") is True
            or ((flight.get("altitude") or 0) <= 300 and (flight.get("ground_speed") or 0) <= 60)
        ):
            signals.add("ground_near_destination")
    return signals


def event_matches_flight(
    event: dict[str, Any] | None,
    identifiers: dict[str, str],
    leg: FlightLeg,
) -> bool:
    """Match an arrival event to both the aircraft and scheduled operation.

    FR24 can emit a landing/gate event for the aircraft's preceding flight just
    after Pilot Tracker starts following its next delayed operation. Aircraft
    identity alone is therefore not sufficient evidence of this leg's arrival.
    """
    if not event:
        return False
    identity_matches = any(
        identifiers.get(key) and str(event.get(key, "")) == identifiers[key]
        for key in ("id", "aircraft_registration", "aircraft_icao_24bit", "callsign")
    )
    if not identity_matches:
        return False
    if normalize_flight_number(event.get("flight_number")) != leg.flight_number:
        return False
    destination = route_code(event, "destination")
    return bool(destination and route_matches(destination, leg.destination))
