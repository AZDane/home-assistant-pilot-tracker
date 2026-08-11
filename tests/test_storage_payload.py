"""Storage-shape expectations that do not require a running HA instance."""

from custom_components.pilot_tracker.models import TripStatus
from custom_components.pilot_tracker.providers.southwest import SouthwestPairingProvider
from tests.test_southwest import SAMPLE


def test_completed_trip_remains_serializable_for_archive():
    trip = SouthwestPairingProvider().parse(SAMPLE, year=2026)
    trip.status = TripStatus.COMPLETE
    payload = trip.to_dict()
    assert payload["status"] == "complete"
    assert len(payload["legs"]) == 7
