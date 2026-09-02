from datetime import timedelta

import pytest

from custom_components.pilot_tracker.models import LegStatus
from custom_components.pilot_tracker.providers.southwest import SouthwestPairingProvider
from custom_components.pilot_tracker.schedule import (
    duplicate_preference,
    preserve_duplicate_progress,
    ScheduleConflictError,
    ScheduleLimitError,
    overlapping_trip_keys,
    trips_equivalent,
    validate_collection_horizon,
    validate_leg_order,
)
from tests.test_southwest import SAMPLE


def test_two_month_collection_is_allowed():
    first = SouthwestPairingProvider().parse(SAMPLE, year=2026)
    second = SouthwestPairingProvider().parse(SAMPLE.replace("PAGR", "NEXT"), year=2026)
    for leg in second.legs:
        leg.scheduled_departure += timedelta(days=60)
        leg.scheduled_arrival += timedelta(days=60)
        leg.date = leg.scheduled_departure.date().isoformat()
    validate_collection_horizon([first], second)


def test_collection_over_62_days_is_rejected():
    first = SouthwestPairingProvider().parse(SAMPLE, year=2026)
    second = SouthwestPairingProvider().parse(SAMPLE.replace("PAGR", "NEXT"), year=2026)
    for leg in second.legs:
        leg.scheduled_departure += timedelta(days=70)
        leg.scheduled_arrival += timedelta(days=70)
        leg.date = leg.scheduled_departure.date().isoformat()
    with pytest.raises(ScheduleLimitError):
        validate_collection_horizon([first], second)


def test_overlapping_loaded_trips_are_both_identified():
    first = SouthwestPairingProvider().parse(SAMPLE, year=2026)
    second = SouthwestPairingProvider().parse(SAMPLE.replace("PAGR", "NEXT"), year=2026)

    assert overlapping_trip_keys([first, second]) == sorted([first.key, second.key])


def test_legacy_calendar_copy_is_recognized_as_exact_duplicate():
    pairing = SouthwestPairingProvider().parse(SAMPLE, year=2026)
    legacy = SouthwestPairingProvider().parse(SAMPLE, year=2026)
    legacy.trip_id = "CAL-2026-08-07"

    assert trips_equivalent(pairing, legacy)
    assert duplicate_preference(pairing) > duplicate_preference(legacy)


def test_duplicate_progress_is_preserved_on_pairing_identifier():
    pairing = SouthwestPairingProvider().parse(SAMPLE, year=2026)
    legacy = SouthwestPairingProvider().parse(SAMPLE, year=2026)
    legacy.trip_id = "CAL-2026-08-07"
    legacy.legs[0].status = LegStatus.COMPLETED
    legacy.legs[1].status = LegStatus.ACTIVE
    legacy.legs[1].tracking_identifiers = {"id": "tracked-aircraft"}
    legacy.current_leg_sequence = 2

    preserve_duplicate_progress(pairing, legacy)

    assert pairing.legs[0].status == LegStatus.COMPLETED
    assert pairing.legs[1].status == LegStatus.ACTIVE
    assert pairing.legs[1].tracking_identifiers == {"id": "tracked-aircraft"}
    assert pairing.current_leg_sequence == 2


def test_nonoverlapping_loaded_trips_are_allowed():
    first = SouthwestPairingProvider().parse(SAMPLE, year=2026)
    second = SouthwestPairingProvider().parse(SAMPLE.replace("PAGR", "NEXT"), year=2026)
    for leg in second.legs:
        leg.scheduled_departure += timedelta(days=14)
        leg.scheduled_arrival += timedelta(days=14)
        leg.date = leg.scheduled_departure.date().isoformat()

    assert overlapping_trip_keys([first, second]) == []


def test_overlapping_legs_inside_one_trip_are_rejected():
    trip = SouthwestPairingProvider().parse(SAMPLE, year=2026)
    trip.legs[1].scheduled_departure = trip.legs[0].scheduled_departure + timedelta(minutes=30)

    with pytest.raises(ScheduleConflictError, match="overlaps"):
        validate_leg_order(trip)
