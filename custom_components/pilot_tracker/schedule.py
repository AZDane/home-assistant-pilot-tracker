"""Safe schedule replacement and merge rules."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

from .models import FlightLeg, LegStatus, Trip, TripStatus


class ScheduleConflictError(ValueError):
    """A schedule change cannot be applied without explicit resolution."""


class ScheduleLimitError(ValueError):
    """The imported schedule collection exceeds the supported horizon."""


def trips_equivalent(first: Trip, second: Trip) -> bool:
    """Return whether two differently named schedules contain the same legs."""
    if len(first.legs) != len(second.legs):
        return False
    return all(
        left.identity == right.identity
        and left.scheduled_departure == right.scheduled_departure
        and left.scheduled_arrival == right.scheduled_arrival
        for left, right in zip(first.legs, second.legs, strict=True)
    )


def duplicate_preference(trip: Trip) -> tuple[int, int, int]:
    """Rank exact duplicates, favoring operational state and real pairing IDs."""
    active = any(leg.status == LegStatus.ACTIVE for leg in trip.legs)
    progressed = sum(leg.status in (LegStatus.COMPLETED, LegStatus.ACTIVE) for leg in trip.legs)
    real_pairing_id = not trip.trip_id.startswith("CAL-")
    # Exact leg equivalence makes it safe to copy progress onto the canonical
    # pairing ID, even when the legacy CAL copy owns the active pointer.
    return int(real_pairing_id), int(active), progressed


def preserve_duplicate_progress(kept: Trip, removed: Trip) -> Trip:
    """Copy completed/active state from an equivalent schedule before removal."""
    removed_by_identity = {leg.identity: leg for leg in removed.legs}
    active_identity = removed.current_leg.identity if removed.current_leg else None
    for leg in kept.legs:
        old = removed_by_identity.get(leg.identity)
        if old and old.status in (LegStatus.COMPLETED, LegStatus.ACTIVE):
            leg.status = old.status
            leg.tracking_identifiers = dict(old.tracking_identifiers)
        if active_identity == leg.identity and leg.status == LegStatus.ACTIVE:
            kept.current_leg_sequence = leg.sequence
    return kept


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


def select_pending_leg(trip: Trip, now: datetime) -> FlightLeg | None:
    """Select the operating leg, or otherwise the earliest future leg."""
    pending = [leg for leg in trip.legs if leg.status == LegStatus.PENDING]
    # Pending legs have no positively identified aircraft, so do not retain an
    # already-arrived leg using the active-flight arrival grace period. Once
    # its scheduled arrival passes, select the earliest future leg instead.
    operating = [
        leg for leg in pending
        if leg.scheduled_departure <= now <= leg.scheduled_arrival
    ]
    if operating:
        return max(operating, key=lambda leg: leg.scheduled_departure)
    future = [leg for leg in pending if leg.scheduled_departure > now]
    return min(future, key=lambda leg: leg.scheduled_departure) if future else None


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
            leg = replace(
                leg,
                status=old.status,
                tracking_identifiers=dict(old.tracking_identifiers),
            )
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
