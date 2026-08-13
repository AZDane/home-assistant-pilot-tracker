from datetime import datetime, timezone

from custom_components.pilot_tracker.arrival import arrival_signals, distance_nm, event_matches_flight
from custom_components.pilot_tracker.models import FlightLeg


def test_distance_and_ground_arrival_evidence():
    assert distance_nm(33.4342, -112.0116, 33.4343, -112.0115) < 1
    flight = {
        "latitude": 33.4342, "longitude": -112.0116,
        "airport_destination_latitude": 33.4343,
        "airport_destination_longitude": -112.0115,
        "on_ground": True,
    }
    assert "ground_near_destination" in arrival_signals(flight, None)


def test_lifecycle_event_is_only_one_signal():
    assert arrival_signals({}, {"event_type": "flightradar24_tracked_landed"}) == {"landed_event"}


def test_stale_same_aircraft_arrival_event_does_not_match_next_leg():
    leg = FlightLeg(
        sequence=1,
        date="2026-08-12",
        flight_number="1129",
        airline="WN",
        origin="PHX",
        destination="MDW",
        scheduled_departure=datetime(2026, 8, 13, 1, 14, tzinfo=timezone.utc),
        scheduled_arrival=datetime(2026, 8, 13, 4, 30, tzinfo=timezone.utc),
    )
    identifiers = {"aircraft_registration": "N123WN"}
    stale_event = {
        "event_type": "flightradar24_tracked_arrived_gate",
        "aircraft_registration": "N123WN",
        "flight_number": "WN4453",
        "airport_destination_code_iata": "PHX",
    }

    assert not event_matches_flight(stale_event, identifiers, leg)


def test_matching_arrival_event_requires_operation_and_destination():
    leg = FlightLeg(
        sequence=1,
        date="2026-08-12",
        flight_number="1129",
        airline="WN",
        origin="PHX",
        destination="MDW",
        scheduled_departure=datetime(2026, 8, 13, 1, 14, tzinfo=timezone.utc),
        scheduled_arrival=datetime(2026, 8, 13, 4, 30, tzinfo=timezone.utc),
    )
    event = {
        "event_type": "flightradar24_tracked_arrived_gate",
        "aircraft_registration": "N123WN",
        "flight_number": "SWA1129",
        "airport_destination_code_icao": "KMDW",
    }

    assert event_matches_flight(event, {"aircraft_registration": "N123WN"}, leg)
