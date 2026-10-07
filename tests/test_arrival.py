from datetime import UTC, datetime, timedelta

import pytest

from custom_components.pilot_tracker.arrival import (
    arrival_complete,
    arrival_signals,
    distance_nm,
    event_matches_flight,
)
from custom_components.pilot_tracker.models import FlightLeg


def test_distance_and_ground_arrival_evidence():
    assert distance_nm(33.4342, -112.0116, 33.4343, -112.0115) < 1
    flight = {
        "latitude": 33.4342,
        "longitude": -112.0116,
        "airport_destination_latitude": 33.4343,
        "airport_destination_longitude": -112.0115,
        "on_ground": True,
    }
    assert "ground_near_destination" in arrival_signals(flight, None)


def test_lifecycle_event_is_only_one_signal():
    assert arrival_signals({}, {"event_type": "flightradar24_tracked_landed"}) == {"landed_event"}


def test_incomplete_fr24_record_uses_scheduled_destination_coordinates():
    flight = {
        "latitude": "33.4342",
        "longitude": "-112.0116",
        "altitude": "N/A",
        "ground_speed": "N/A",
    }

    signals = arrival_signals(flight, None, (33.4343, -112.0115))

    assert "destination_near" in signals
    assert "ground_near_destination" not in signals


@pytest.mark.parametrize(
    ("telemetry", "expected_signal"),
    [
        ({"on_ground": 1}, "ground_near_destination"),
        ({"on_ground": "1"}, "ground_near_destination"),
        ({"altitude": "250", "ground_speed": "55"}, "ground_near_destination"),
        ({"time_real_arrival": "1791346440"}, "reported_arrival_at_destination"),
    ],
)
def test_numeric_and_string_fr24_values_are_accepted(telemetry, expected_signal):
    flight = {
        "latitude": 33.4342,
        "longitude": -112.0116,
        **telemetry,
    }

    assert expected_signal in arrival_signals(flight, None, (33.4343, -112.0115))


def test_fr24_actual_arrival_at_destination_is_accepted_without_telemetry():
    flight = {
        "latitude": "33.4342",
        "longitude": "-112.0116",
        "altitude": "N/A",
        "ground_speed": "N/A",
        "time_real_arrival": 1791346440,
    }

    signals = arrival_signals(flight, None, (33.4343, -112.0115))

    assert "reported_arrival_at_destination" in signals


def test_fr24_actual_arrival_away_from_destination_is_not_accepted():
    flight = {
        "latitude": 34.2000,
        "longitude": -111.9000,
        "time_real_arrival": 1791346440,
    }

    signals = arrival_signals(flight, None, (33.4343, -112.0115))

    assert "reported_arrival_at_destination" not in signals


def test_approach_go_around_and_diversion_never_completes_from_proximity_alone():
    scheduled_arrival = datetime(2026, 10, 7, 4, 40, tzinfo=UTC)
    approach = {
        "latitude": "33.4342",
        "longitude": "-112.0116",
        "altitude": "1200",
        "ground_speed": "145",
        "on_ground": "N/A",
    }
    diverted = {
        "latitude": 34.2000,
        "longitude": -111.9000,
        "altitude": 18000,
        "ground_speed": 390,
    }
    destination = (33.4343, -112.0115)
    evidence = arrival_signals(approach, None, destination)
    evidence |= arrival_signals(diverted, None, destination)

    assert evidence == {"destination_near"}

    assert not arrival_complete(evidence, 0, scheduled_arrival + timedelta(minutes=30), scheduled_arrival)
    assert not arrival_complete(evidence, 0, scheduled_arrival + timedelta(hours=3), scheduled_arrival)


def test_stale_explicit_ground_position_completes_after_grace_period():
    scheduled_arrival = datetime(2026, 10, 7, 4, 40, tzinfo=UTC)

    assert arrival_complete(
        {"ground_near_destination"},
        1,
        scheduled_arrival + timedelta(hours=2),
        scheduled_arrival,
    )


@pytest.mark.parametrize("event_signal", ["landed_event", "gate_event"])
def test_matching_arrival_event_completes_before_schedule(event_signal):
    scheduled_arrival = datetime(2026, 10, 7, 4, 40, tzinfo=UTC)

    assert arrival_complete({event_signal}, 0, scheduled_arrival - timedelta(minutes=26), scheduled_arrival)


@pytest.mark.parametrize(
    ("persisted_evidence", "persisted_samples"),
    [
        ({"gate_event"}, 0),
        ({"landed_event", "ground_near_destination"}, 0),
        ({"ground_near_destination"}, 2),
    ],
)
def test_existing_persisted_arrival_evidence_remains_compatible(persisted_evidence, persisted_samples):
    scheduled_arrival = datetime(2026, 10, 7, 4, 40, tzinfo=UTC)

    assert arrival_complete(
        persisted_evidence,
        persisted_samples,
        scheduled_arrival,
        scheduled_arrival,
    )


@pytest.mark.parametrize(
    "event_type",
    ["flightradar24_tracked_landed", "flightradar24_tracked_arrived_gate"],
)
def test_stale_same_aircraft_arrival_event_does_not_match_next_leg(event_type):
    leg = FlightLeg(
        sequence=1,
        date="2026-08-12",
        flight_number="1129",
        airline="WN",
        origin="PHX",
        destination="MDW",
        scheduled_departure=datetime(2026, 8, 13, 1, 14, tzinfo=UTC),
        scheduled_arrival=datetime(2026, 8, 13, 4, 30, tzinfo=UTC),
    )
    identifiers = {"aircraft_registration": "N123WN"}
    stale_event = {
        "event_type": event_type,
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
        scheduled_departure=datetime(2026, 8, 13, 1, 14, tzinfo=UTC),
        scheduled_arrival=datetime(2026, 8, 13, 4, 30, tzinfo=UTC),
    )
    event = {
        "event_type": "flightradar24_tracked_arrived_gate",
        "aircraft_registration": "N123WN",
        "flight_number": "SWA1129",
        "airport_destination_code_icao": "KMDW",
    }

    assert event_matches_flight(event, {"aircraft_registration": "N123WN"}, leg)
