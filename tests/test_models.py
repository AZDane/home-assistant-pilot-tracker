from custom_components.pilot_tracker.models import Trip
from custom_components.pilot_tracker.providers.southwest import SouthwestPairingProvider
from tests.test_southwest import SAMPLE


def test_trip_storage_round_trip():
    trip = SouthwestPairingProvider().parse(SAMPLE, year=2026)
    assert Trip.from_dict(trip.to_dict()).to_dict() == trip.to_dict()


def test_leg_id_rejects_reused_flight_on_another_date():
    trip = SouthwestPairingProvider().parse(SAMPLE, year=2026)
    first = trip.legs[0]
    changed = first.to_dict()
    changed["date"] = "2026-08-08"
    assert Trip.from_dict({**trip.to_dict(), "legs": [changed]}).legs[0].identity != first.identity
