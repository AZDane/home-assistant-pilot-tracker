"""Coordinator cleanup state repair tests."""

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from custom_components.pilot_tracker.coordinator import PilotTrackerCoordinator
from custom_components.pilot_tracker.models import FlightLeg, Trip, TripStatus
from custom_components.pilot_tracker.state_machine import PilotTrackerState

ZONE = ZoneInfo("America/Phoenix")


def _calendar_trip(identifier, month, day):
    departure = datetime(2026, month, day, 10, tzinfo=ZONE)
    return Trip(
        identifier,
        "crewhub_calendar",
        "airport_local",
        [
            FlightLeg(
                1,
                departure.date().isoformat(),
                "123",
                "WN",
                "PHX",
                "LAS",
                departure,
                departure.replace(hour=11),
            )
        ],
        metadata={"calendar_entity_id": "calendar.house"},
    )


class _Store:
    def __init__(self, trips, selected):
        self.trips = trips
        self.selected = selected
        self.archived = []

    def get(self, key):
        return next((trip for trip in self.trips if trip.key == key), None)

    async def async_archive(self, trip):
        self.archived.append(trip)
        self.trips.remove(trip)
        if self.selected == trip.key:
            self.selected = None

    async def async_select(self, key):
        self.selected = key


def test_cleanup_archives_expired_selection_and_selects_future_trip():
    expired = _calendar_trip("SEP", 9, 10)
    future = _calendar_trip("NOV", 11, 12)
    store = _Store([expired, future], expired.key)
    coordinator = object.__new__(PilotTrackerCoordinator)
    coordinator.store = store
    coordinator.trip = expired
    coordinator.schedule_conflicts = []
    coordinator.last_rejection = None
    coordinator.state = PilotTrackerState.WAITING_FOR_DUTY
    coordinator._choose_operational_trip = lambda preferred: future
    updates = []
    coordinator.async_set_updated_data = updates.append

    archived = asyncio.run(
        coordinator.async_cleanup_expired_calendar_trips(
            "calendar.house",
            datetime(2026, 11, 9, 8, tzinfo=ZONE),
        )
    )

    assert archived == 1
    assert expired.status == TripStatus.ARCHIVED
    assert store.archived == [expired]
    assert coordinator.trip is future
    assert store.selected == future.key
    assert coordinator.state == PilotTrackerState.WAITING_FOR_DUTY
    assert updates == [None]
