"""Public-HA-surface adapter for the Flightradar24 custom integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.const import ATTR_ENTITY_ID, EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

_LOGGER = logging.getLogger(__name__)
FR24_DOMAIN = "flightradar24"
FR24_EVENTS = (
    "flightradar24_tracked_took_off",
    "flightradar24_tracked_landed",
    "flightradar24_tracked_arrived_gate",
    "flightradar24_tracked_left_gate",
)


class FlightRadar24NotReady(RuntimeError):
    """The required upstream entity surface is not available."""


@dataclass(frozen=True, slots=True)
class FlightRadar24Entities:
    config_entry_id: str
    add_track: str
    remove_track: str
    additional_tracked: str
    tracker: str | None = None


class FlightRadar24Adapter:
    """Discover and use FR24 exclusively through supported HA interfaces."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.entities: FlightRadar24Entities | None = None
        self.last_event: dict[str, Any] | None = None
        self._on_update: Callable[[], None] | None = None
        self._unsubscribers: list[Callable[[], None]] = []

    async def async_setup(self, on_update: Callable[[], None]) -> None:
        self._on_update = on_update
        self._unsubscribers.append(self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._state_changed))
        for event_type in FR24_EVENTS:
            self._unsubscribers.append(self.hass.bus.async_listen(event_type, self._lifecycle_event))
        self.entities = self._discover_entities()

    async def async_shutdown(self) -> None:
        while self._unsubscribers:
            self._unsubscribers.pop()()

    @property
    def available(self) -> bool:
        if not self.entities:
            return False
        state = self.hass.states.get(self.entities.additional_tracked)
        return state is not None and state.state != "unavailable"

    @property
    def flights(self) -> list[dict[str, Any]]:
        if not self.entities:
            return []
        state = self.hass.states.get(self.entities.additional_tracked)
        if state is None:
            return []
        flights = state.attributes.get("flights", [])
        return [dict(flight) for flight in flights if isinstance(flight, dict)]

    async def async_start(self, identifier: str) -> None:
        entities = self._ensure_entities()
        await self.hass.services.async_call(
            "text", "set_value",
            {ATTR_ENTITY_ID: entities.add_track, "value": identifier},
            blocking=True,
        )

    async def async_stop(self, identifier: str) -> None:
        entities = self._ensure_entities()
        await self.hass.services.async_call(
            "text", "set_value",
            {ATTR_ENTITY_ID: entities.remove_track, "value": identifier},
            blocking=True,
        )

    def _ensure_entities(self) -> FlightRadar24Entities:
        if self.entities is None:
            self.entities = self._discover_entities()
        return self.entities

    def refresh_discovery(self) -> bool:
        """Rediscover entity IDs after an upstream reload or registry change."""
        try:
            discovered = self._discover_entities()
        except FlightRadar24NotReady:
            self.entities = None
            return False
        self.entities = discovered
        return True

    def _discover_entities(self) -> FlightRadar24Entities:
        entries = self.hass.config_entries.async_entries(FR24_DOMAIN)
        if not entries:
            raise FlightRadar24NotReady("Flightradar24 is not configured")
        registry = er.async_get(self.hass)
        complete: list[FlightRadar24Entities] = []
        for config_entry in entries:
            found: dict[str, str] = {}
            for entity in er.async_entries_for_config_entry(registry, config_entry.entry_id):
                unique_id = entity.unique_id
                if entity.entity_id.startswith("text.") and unique_id.endswith("_flightradar24_add_track"):
                    found["add_track"] = entity.entity_id
                elif entity.entity_id.startswith("text.") and unique_id.endswith("_flightradar24_remove_track"):
                    found["remove_track"] = entity.entity_id
                elif entity.entity_id.startswith("sensor.") and unique_id.endswith("_flightradar24_additional_tracked"):
                    found["additional_tracked"] = entity.entity_id
                elif entity.entity_id.startswith("device_tracker.") and unique_id.endswith("_flightradar24"):
                    found["tracker"] = entity.entity_id
            if all(key in found for key in ("add_track", "remove_track", "additional_tracked")):
                complete.append(FlightRadar24Entities(config_entry.entry_id, **found))
        if not complete:
            raise FlightRadar24NotReady(
                "Flightradar24 add/remove controls or additional-tracked sensor were not found"
            )
        if len(complete) > 1:
            raise FlightRadar24NotReady("Multiple Flightradar24 instances require explicit selection")
        return complete[0]

    @callback
    def _state_changed(self, event: Event) -> None:
        if self.entities and event.data.get("entity_id") in (
            self.entities.additional_tracked, self.entities.tracker
        ):
            if self._on_update:
                self._on_update()

    @callback
    def _lifecycle_event(self, event: Event) -> None:
        self.last_event = {"event_type": event.event_type, **event.data}
        _LOGGER.debug("Received FR24 lifecycle event %s", event.event_type)
        if self._on_update:
            self._on_update()
