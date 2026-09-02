from dataclasses import replace

import pytest

from custom_components.pilot_tracker.models import LegStatus
from custom_components.pilot_tracker.providers.southwest import SouthwestPairingProvider
from custom_components.pilot_tracker.schedule import ScheduleConflictError, merge_trip, select_pending_leg
from tests.test_southwest import SAMPLE


def trip():
    return SouthwestPairingProvider().parse(SAMPLE, year=2026)


def test_revision_preserves_completed_and_active_legs():
    existing = trip()
    existing.legs[0].status = LegStatus.COMPLETED
    existing.legs[1].status = LegStatus.ACTIVE
    existing.current_leg_sequence = 2
    imported = trip()
    imported.legs[-1] = replace(imported.legs[-1], scheduled_departure=imported.legs[-1].scheduled_departure.replace(minute=12))
    merged = merge_trip(existing, imported)
    assert [leg.status for leg in merged.legs[:2]] == [LegStatus.COMPLETED, LegStatus.ACTIVE]
    assert merged.legs[-1].scheduled_departure.minute == 12


def test_revision_cannot_change_current_leg():
    existing = trip()
    existing.legs[1].status = LegStatus.ACTIVE
    existing.current_leg_sequence = 2
    imported = trip()
    imported.legs[1] = replace(imported.legs[1], destination="LAS")
    with pytest.raises(ScheduleConflictError, match="active leg"):
        merge_trip(existing, imported)


def test_different_active_trip_is_not_silently_replaced():
    existing = trip()
    imported = trip()
    imported.trip_id = "DIFFERENT"
    with pytest.raises(ScheduleConflictError, match="cannot be replaced"):
        merge_trip(existing, imported)


def test_revision_drops_stale_pending_current_leg_pointer():
    existing = trip()
    existing.current_leg_sequence = 1
    imported = trip()
    merged = merge_trip(existing, imported)
    assert merged.current_leg_sequence is None


def test_mid_duty_selection_skips_finished_legs():
    trip = SouthwestPairingProvider().parse(SAMPLE, year=2026)
    now = trip.legs[2].scheduled_departure + timedelta(minutes=5)

    assert select_pending_leg(trip, now) == trip.legs[2]


def test_between_legs_selection_chooses_next_future_leg():
    trip = SouthwestPairingProvider().parse(SAMPLE, year=2026)
    now = trip.legs[0].scheduled_arrival + timedelta(hours=2)

    assert select_pending_leg(trip, now) == trip.legs[1]


def test_pending_leg_is_not_retained_after_scheduled_arrival():
    trip = SouthwestPairingProvider().parse(SAMPLE, year=2026)
    now = trip.legs[0].scheduled_arrival + timedelta(minutes=1)

    assert select_pending_leg(trip, now) == trip.legs[1]


def test_revision_preserves_active_aircraft_identifiers():
    existing = SouthwestPairingProvider().parse(SAMPLE, year=2026)
    existing.current_leg_sequence = 1
    existing.legs[0].status = LegStatus.ACTIVE
    existing.legs[0].tracking_identifiers = {
        "aircraft_registration": "N123WN",
        "id": "flight-id",
    }
    revised = SouthwestPairingProvider().parse(SAMPLE, year=2026)

    merged = merge_trip(existing, revised)

    assert merged.legs[0].tracking_identifiers == {
        "aircraft_registration": "N123WN",
        "id": "flight-id",
    }
