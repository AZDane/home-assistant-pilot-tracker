from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.pilot_tracker.models import LegStatus
from custom_components.pilot_tracker.providers.southwest import SouthwestPairingProvider
from custom_components.pilot_tracker.schedule import (
    duplicate_preference,
    preserve_duplicate_progress,
    ScheduleConflictError,
    ScheduleLimitError,
    overlapping_trip_keys,
    stale_calendar_trip_keys,
    trips_equivalent,
    validate_collection_horizon,
    validate_leg_order,
)
from tests.test_southwest import SAMPLE


def test_collection_at_62_day_boundary_is_allowed():
    first = SouthwestPairingProvider().parse(SAMPLE, year=2026)
    second = SouthwestPairingProvider().parse(SAMPLE.replace("PAGR", "NEXT"), year=2026)
    departures = [leg.scheduled_departure for leg in first.legs]
    offset = timedelta(days=62) - (max(departures) - min(departures))
    for leg in second.legs:
        leg.scheduled_departure += offset
        leg.scheduled_arrival += offset
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


def test_legacy_calendar_copy_with_revised_times_is_recognized_as_duplicate():
    pairing = SouthwestPairingProvider().parse(SAMPLE, year=2026)
    legacy = SouthwestPairingProvider().parse(SAMPLE, year=2026)
    legacy.trip_id = "CAL-2026-08-07"
    legacy.legs[0].scheduled_departure -= timedelta(minutes=15)
    legacy.legs[-1].scheduled_arrival += timedelta(minutes=20)

    assert trips_equivalent(pairing, legacy)


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


def test_calendar_reconciliation_removes_only_missing_owned_trips_in_window():
    zone = ZoneInfo("America/Phoenix")
    seen = SouthwestPairingProvider().parse(SAMPLE, year=2026)
    seen.source = "crewhub_calendar"
    seen.metadata["calendar_entity_id"] = "calendar.crew"

    stale = SouthwestPairingProvider().parse(SAMPLE.replace("PAGR", "STALE"), year=2026)
    stale.source = "crewhub_calendar"
    stale.metadata["calendar_entity_id"] = "calendar.crew"
    for leg in stale.legs:
        leg.scheduled_departure += timedelta(days=14)
        leg.scheduled_arrival += timedelta(days=14)
        leg.date = leg.scheduled_departure.date().isoformat()

    manual = SouthwestPairingProvider().parse(SAMPLE.replace("PAGR", "MANUAL"), year=2026)
    for leg in manual.legs:
        leg.scheduled_departure += timedelta(days=21)
        leg.scheduled_arrival += timedelta(days=21)
        leg.date = leg.scheduled_departure.date().isoformat()

    other_calendar = SouthwestPairingProvider().parse(SAMPLE.replace("PAGR", "OTHER"), year=2026)
    other_calendar.source = "crewhub_calendar"
    other_calendar.metadata["calendar_entity_id"] = "calendar.other"
    for leg in other_calendar.legs:
        leg.scheduled_departure += timedelta(days=28)
        leg.scheduled_arrival += timedelta(days=28)
        leg.date = leg.scheduled_departure.date().isoformat()

    keys = stale_calendar_trip_keys(
        [seen, stale, manual, other_calendar],
        "calendar.crew",
        {seen.key},
        datetime(2026, 8, 1, tzinfo=zone),
        datetime(2026, 10, 1, tzinfo=zone),
    )

    assert keys == [stale.key]


def test_calendar_reconciliation_does_not_remove_trip_outside_query_window():
    zone = ZoneInfo("America/Phoenix")
    old = SouthwestPairingProvider().parse(SAMPLE, year=2026)
    old.source = "crewhub_calendar"
    old.metadata["calendar_entity_id"] = "calendar.crew"

    assert stale_calendar_trip_keys(
        [old],
        "calendar.crew",
        set(),
        datetime(2026, 9, 1, tzinfo=zone),
        datetime(2026, 11, 1, tzinfo=zone),
    ) == []
