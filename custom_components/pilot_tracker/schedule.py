"""Safe schedule replacement and merge rules."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from .models import LegStatus, Trip, TripStatus


class ScheduleConflictError(ValueError):
    """A schedule change cannot be applied without explicit resolution."""


class ScheduleLimitError(ValueError):
    """The imported schedule collection exceeds the supported horizon."""


def validate_collection_horizon(trips: list[Trip], imported: Trip) -> None:
    all_trips = [trip for trip in trips if trip.key != imported.key] + [imported]
    dates = [leg.scheduled_departure for trip in all_trips for leg in trip.legs]
    if dates and max(dates) - min(dates) > timedelta(days=62):
        raise ScheduleLimitError("Loaded schedules span more than 62 days")


def validate_leg_order(trip: Trip) -> None:
    """Reject a malformed pairing containing simultaneously scheduled legs."""
    ordered = sorted(trip.legs, key=lambda leg: leg.scheduled_departure)
    for previous, current in zip(ordered, ordered[1:], strict=False):
        if current.scheduled_departure < previous.scheduled_arrival:
            raise ScheduleConflictError(
                f"{current.airline}{current.flight_number} overlaps "
                f"{previous.airline}{previous.flight_number}"
            )


def overlapping_trip_keys(trips: list[Trip]) -> list[str]:
    """Return every trip containing a leg that overlaps another trip's leg."""
    conflicts: set[str] = set()
    active = [trip for trip in trips if trip.status == TripStatus.ACTIVE]
    for index, first in enumerate(active):
        for second in active[index + 1:]:
            if trips_overlap(first, second):
                conflicts.update((first.key, second.key))
    return sorted(conflicts)


def trips_overlap(first: Trip, second: Trip) -> bool:
    """Return whether any flight intervals in two trips intersect."""
    return any(
        left.scheduled_departure < right.scheduled_arrival
        and right.scheduled_departure < left.scheduled_arrival
        for left in first.legs for right in second.legs
    )


def merge_trip(existing: Trip | None, imported: Trip) -> Trip:
    """Merge an imported revision while preserving operational progress."""
    if existing is None or existing.status in (TripStatus.COMPLETE, TripStatus.ARCHIVED):
        return imported
    if existing.trip_id != imported.trip_id:
        raise ScheduleConflictError(
            f"Active trip {existing.trip_id} cannot be replaced by {imported.trip_id}"
        )

    active = existing.current_leg
    operationally_active = active is not None and active.status == LegStatus.ACTIVE
    if operationally_active:
        imported_at_sequence = next(
            (leg for leg in imported.legs if leg.sequence == active.sequence), None
        )
        if imported_at_sequence is None or imported_at_sequence.identity != active.identity:
            raise ScheduleConflictError("The revision changes the currently active leg")

    old_by_identity = {leg.identity: leg for leg in existing.legs}
    merged_legs = []
    for leg in imported.legs:
        old = old_by_identity.get(leg.identity)
        if old and old.status in (LegStatus.COMPLETED, LegStatus.ACTIVE):
            leg = replace(leg, status=old.status)
        merged_legs.append(leg)

    completed_missing = [
        leg for leg in existing.legs
        if leg.status == LegStatus.COMPLETED and leg.identity not in {item.identity for item in merged_legs}
    ]
    if completed_missing:
        raise ScheduleConflictError("The revision removes a completed leg")

    imported.legs = merged_legs
    # A stale pending/error resolution pointer must not pin a revised schedule
    # to an already-ended duty. Only a positively identified active aircraft
    # has enough operational state to retain current-leg ownership.
    imported.current_leg_sequence = existing.current_leg_sequence if operationally_active else None
    return imported
