from custom_components.pilot_tracker.flight_validation import validate_candidate
from custom_components.pilot_tracker.providers.southwest import SouthwestPairingProvider
from tests.test_southwest import SAMPLE


def candidate(leg):
    return {
        "id": "abc123",
        "flight_number": "WN3206",
        "airline_iata": "WN",
        "airport_origin_code_iata": "PHX",
        "airport_destination_code_iata": "IND",
        "time_scheduled_departure": leg.scheduled_departure.timestamp(),
        "tracked_type": "live",
        "latitude": 33.4,
        "longitude": -112.0,
        "aircraft_registration": "N123WN",
    }


def test_accepts_fully_matching_live_candidate():
    leg = SouthwestPairingProvider().parse(SAMPLE, year=2026).legs[0]
    assert validate_candidate(leg, candidate(leg)).accepted


def test_accepts_southwest_icao_when_iata_is_missing():
    leg = SouthwestPairingProvider().parse(SAMPLE, year=2026).legs[0]
    flight = candidate(leg)
    flight["airline_iata"] = None
    flight["airline_icao"] = "SWA"

    assert validate_candidate(leg, flight).accepted


def test_accepts_airport_icao_when_iata_route_is_missing():
    leg = SouthwestPairingProvider().parse(SAMPLE, year=2026).legs[0]
    flight = candidate(leg)
    flight["airport_origin_code_iata"] = None
    flight["airport_origin_code_icao"] = "KPHX"
    flight["airport_destination_code_iata"] = None
    flight["airport_destination_code_icao"] = "KIND"

    assert validate_candidate(leg, flight).accepted


def test_reports_missing_route_data_separately_from_mismatch():
    leg = SouthwestPairingProvider().parse(SAMPLE, year=2026).legs[0]
    flight = candidate(leg)
    flight["airport_origin_code_iata"] = None

    assert validate_candidate(leg, flight).reason == "missing_origin_data"


def test_rejects_reused_number_on_wrong_day():
    leg = SouthwestPairingProvider().parse(SAMPLE, year=2026).legs[0]
    flight = candidate(leg)
    flight["time_scheduled_departure"] += 24 * 60 * 60
    assert validate_candidate(leg, flight).reason == "departure_window_mismatch"


def test_rejects_route_and_airline_mismatch():
    leg = SouthwestPairingProvider().parse(SAMPLE, year=2026).legs[0]
    flight = candidate(leg)
    flight["airport_destination_code_iata"] = "DEN"
    assert validate_candidate(leg, flight).reason == "destination_mismatch"
    flight = candidate(leg)
    flight["airline_iata"] = "AA"
    assert validate_candidate(leg, flight).reason == "airline_mismatch"


def test_diversion_segments_accept_original_through_route():
    trip = SouthwestPairingProvider().parse("""Trip DIV1 dated 20Jun26
HERB TIME/ESTIMATED
20 Jun 296 ELP 0102 DAL 0358 7A8 256 130 DV
20 Jun 296 DAL 0528 SAT 0626 7A8 058
""")
    diverted, continuation = trip.legs
    flight = candidate(diverted)
    flight.update({
        "flight_number": "WN296",
        "airport_origin_code_iata": "ELP",
        "airport_destination_code_iata": "SAT",
    })

    assert diverted.qualifier == "DV"
    assert validate_candidate(diverted, flight).accepted

    flight["time_scheduled_departure"] = continuation.scheduled_departure.timestamp()
    assert continuation.qualifier == "DV-CONT"
    assert validate_candidate(continuation, flight).accepted
