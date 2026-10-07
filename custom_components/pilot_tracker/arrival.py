"""Arrival evidence helpers independent of Home Assistant."""

from __future__ import annotations

from datetime import datetime, timedelta
from math import asin, cos, isfinite, radians, sin, sqrt
from typing import Any

from .flight_validation import normalize_flight_number, route_code, route_matches
from .models import FlightLeg


def distance_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in nautical miles."""
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 3440.065 * 2 * asin(sqrt(a))


def _number(value: Any) -> float | None:
    """Return a finite number for the mixed values exposed by FR24."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _on_ground(value: Any) -> bool:
    """Normalize the boolean, numeric, and text ground flags used by FR24."""
    if value is True or value == 1:
        return True
    return isinstance(value, str) and value.strip().lower() in {"true", "1", "yes"}


def arrival_signals(
    flight: dict[str, Any],
    lifecycle_event: dict[str, Any] | None,
    destination_coordinates: tuple[float, float] | None = None,
) -> set[str]:
    signals: set[str] = set()
    if lifecycle_event:
        event_type = lifecycle_event.get("event_type", "")
        if event_type.endswith("_arrived_gate"):
            signals.add("gate_event")
        elif event_type.endswith("_landed"):
            signals.add("landed_event")
    latitude = _number(flight.get("latitude"))
    longitude = _number(flight.get("longitude"))
    destination_latitude = _number(flight.get("airport_destination_latitude"))
    destination_longitude = _number(flight.get("airport_destination_longitude"))
    if (destination_latitude is None or destination_longitude is None) and destination_coordinates:
        destination_latitude, destination_longitude = destination_coordinates
    coordinates = (latitude, longitude, destination_latitude, destination_longitude)
    if all(value is not None for value in coordinates):
        proximity = distance_nm(*coordinates)  # type: ignore[arg-type]
        if proximity <= 15:
            signals.add("destination_near")
        altitude = _number(flight.get("altitude"))
        ground_speed = _number(flight.get("ground_speed"))
        actual_arrival = _number(flight.get("time_real_arrival"))
        low_and_slow = altitude is not None and ground_speed is not None and altitude <= 300 and ground_speed <= 60
        if proximity <= 5 and actual_arrival is not None and actual_arrival > 0:
            signals.add("reported_arrival_at_destination")
        if proximity <= 5 and (_on_ground(flight.get("on_ground")) or low_and_slow):
            signals.add("ground_near_destination")
    return signals


def arrival_complete(
    evidence: set[str],
    ground_near_samples: int,
    now: datetime,
    scheduled_arrival: datetime,
) -> bool:
    """Return whether independent or conservative fallback evidence proves arrival."""
    return (
        "gate_event" in evidence
        or "landed_event" in evidence
        or "reported_arrival_at_destination" in evidence
        or ("ground_near_destination" in evidence and ground_near_samples >= 2)
        # Preserve the conservative timeout for a single explicit ground
        # sample. Proximity by itself is never sufficient: a flight may fly an
        # approach, go around, and divert.
        or (now >= scheduled_arrival + timedelta(hours=2) and "ground_near_destination" in evidence)
    )


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
