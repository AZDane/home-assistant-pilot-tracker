"""Persistent trip storage."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import STORAGE_KEY, STORAGE_VERSION
from .models import Trip


class TripStore:
    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._archives: list[dict] = []
        self._trips: dict[str, Trip] = {}
        self._active_trip_key: str | None = None

    async def async_load(self) -> Trip | None:
        data = await self._store.async_load()
        self._archives = list((data or {}).get("archived_trips", []))
        if data and data.get("trips"):
            self._trips = {
                item.key: item for item in (Trip.from_dict(raw) for raw in data["trips"])
            }
            self._active_trip_key = data.get("active_trip_key")
        elif data and data.get("active_trip"):
            trip = Trip.from_dict(data["active_trip"])
            self._trips = {trip.key: trip}
            self._active_trip_key = trip.key
        return self.active_trip

    async def async_save(self, trip: Trip | None) -> None:
        if trip is not None:
            self._trips[trip.key] = trip
            self._active_trip_key = trip.key
        await self._write()

    async def _write(self) -> None:
        await self._store.async_save({
            "trips": [trip.to_dict() for trip in self.trips],
            "active_trip_key": self._active_trip_key,
            "archived_trips": self._archives,
        })

    async def async_remove(self, trip_key: str) -> None:
        self._trips.pop(trip_key, None)
        if self._active_trip_key == trip_key:
            self._active_trip_key = None
        await self._write()

    async def async_select(self, trip_key: str | None) -> None:
        self._active_trip_key = trip_key if trip_key in self._trips else None
        await self._write()

    async def async_archive(self, trip: Trip) -> None:
        self._archives.append(trip.to_dict())
        self._archives = self._archives[-20:]
        self._trips.pop(trip.key, None)
        if self._active_trip_key == trip.key:
            self._active_trip_key = None
        await self._write()

    @property
    def archived_count(self) -> int:
        return len(self._archives)

    @property
    def trips(self) -> list[Trip]:
        return sorted(self._trips.values(), key=lambda trip: trip.legs[0].scheduled_departure)

    @property
    def active_trip(self) -> Trip | None:
        return self._trips.get(self._active_trip_key) if self._active_trip_key else None

    def get(self, trip_key: str) -> Trip | None:
        return self._trips.get(trip_key)
