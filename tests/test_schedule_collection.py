from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.pilot_tracker.models import LegStatus
from custom_components.pilot_tracker.providers.southwest import SouthwestPairingProvider
from custom_components.pilot_tracker.schedule import (
    ScheduleConflictError,
    ScheduleLimitError,
    duplicate_preference,
    expired_calendar_trip_keys,
    overlapping_trip_keys,
    preserve_duplicate_progress,
    stale_calendar_trip_keys,
    trip_has_active_tracking,
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


def _shifted_trip(identifier, offset, *, calendar=True):
    trip = SouthwestPairingProvider().parse(SAMPLE.replace("PAGR", identifier), year=2026)
    for leg in trip.legs:
        leg.scheduled_departure += offset
        leg.scheduled_arrival += offset
        leg.date = leg.scheduled_departure.date().isoformat()
    if calendar:
        trip.source = "crewhub_calendar"
        trip.metadata["calendar_entity_id"] = "calendar.house"
    return trip


def test_expired_calendar_cleanup_breaks_old_schedule_horizon_deadlock():
    zone = ZoneInfo("America/Phoenix")
    now = datetime(2026, 11, 9, 8, tzinfo=zone)
    old = _shifted_trip("SEP", timedelta(days=34))  # Starts September 10.
    october = _shifted_trip("OCT", timedelta(days=72))
    november_12 = _shifted_trip("NOV12", timedelta(days=97))
    november_19 = _shifted_trip("NOV19", timedelta(days=104))
    november_25 = _shifted_trip("NOV25", timedelta(days=110))

    loaded = [old, october]
    with pytest.raises(ScheduleLimitError):
        validate_collection_horizon(loaded, november_25)

    expired = set(expired_calendar_trip_keys(loaded, "calendar.house", now))
    operational = [trip for trip in loaded if trip.key not in expired]
    for imported in (november_12, november_19, november_25):
        validate_collection_horizon(operational, imported)
        operational.append(imported)

    assert expired == {old.key, october.key}
    assert min(
        leg.scheduled_departure
        for trip in operational
        for leg in trip.legs
        if leg.scheduled_departure > now
    ).date().isoformat() == "2026-11-12"


def test_expired_calendar_cleanup_is_independent_of_query_lookback():
    zone = ZoneInfo("America/Phoenix")
    old = _shifted_trip("OLD", timedelta(days=34))

    assert expired_calendar_trip_keys(
        [old],
        "calendar.house",
        datetime(2026, 11, 9, 8, tzinfo=zone),
    ) == [old.key]


def test_expired_calendar_cleanup_preserves_manual_and_other_calendar_trips():
    zone = ZoneInfo("America/Phoenix")
    manual = _shifted_trip("MANUAL", timedelta(days=34), calendar=False)
    other = _shifted_trip("OTHER", timedelta(days=34))
    other.metadata["calendar_entity_id"] = "calendar.other"

    assert expired_calendar_trip_keys(
        [manual, other],
        "calendar.house",
        datetime(2026, 11, 9, 8, tzinfo=zone),
    ) == []


def test_expired_calendar_cleanup_preserves_any_positive_tracking_state():
    zone = ZoneInfo("America/Phoenix")
    active = _shifted_trip("ACTIVE", timedelta(days=34))
    active.legs[0].status = LegStatus.ACTIVE
    identified = _shifted_trip("IDENTIFIED", timedelta(days=34))
    identified.legs[0].tracking_identifiers = {"id": "tracked-flight"}

    assert trip_has_active_tracking(active)
    assert trip_has_active_tracking(identified)
    assert expired_calendar_trip_keys(
        [active, identified],
        "calendar.house",
        datetime(2026, 11, 9, 8, tzinfo=zone),
    ) == []


def test_completed_aircraft_history_does_not_pin_an_expired_trip():
    zone = ZoneInfo("America/Phoenix")
    completed = _shifted_trip("COMPLETED", timedelta(days=34))
    completed.legs[0].status = LegStatus.COMPLETED
    completed.legs[0].tracking_identifiers = {"id": "historical-flight"}

    assert not trip_has_active_tracking(completed)
    assert expired_calendar_trip_keys(
        [completed],
        "calendar.house",
        datetime(2026, 11, 9, 8, tzinfo=zone),
    ) == [completed.key]


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
