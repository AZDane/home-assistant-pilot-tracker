"""Primary pilot device tracker scaffold."""

from homeassistant.components.device_tracker import TrackerEntity
from homeassistant.components.device_tracker.const import SourceType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([PilotDeviceTracker(hass.data[DOMAIN][entry.entry_id])])


class PilotDeviceTracker(CoordinatorEntity, TrackerEntity):
    _attr_name = "Pilot"
    _attr_unique_id = "pilot_tracker_pilot"
    _attr_icon = "mdi:airplane"

    @property
    def source_type(self):
        return SourceType.GPS

    @property
    def latitude(self):
        flight = self.coordinator.accepted_flight
        return flight.get("latitude") if flight else None

    @property
    def longitude(self):
        flight = self.coordinator.accepted_flight
        return flight.get("longitude") if flight else None

    @property
    def available(self):
        flight = self.coordinator.accepted_flight
        return bool(
            self.coordinator.tracking_position_fresh
            and flight
            and flight.get("latitude") is not None
            and flight.get("longitude") is not None
        )

    @property
    def extra_state_attributes(self):
        leg = self.coordinator.current_leg
        flight = self.coordinator.accepted_flight or {}
        return {
            "tracking_source": "flightradar24" if flight else None,
            "flight_number": leg.flight_number if leg else None,
            "origin": leg.origin if leg else None,
            "destination": leg.destination if leg else None,
            "aircraft_registration": flight.get("aircraft_registration"),
            "callsign": flight.get("callsign"),
            "altitude": flight.get("altitude"),
            "ground_speed": flight.get("ground_speed"),
            "heading": flight.get("heading"),
            "scheduled_departure": leg.scheduled_departure.isoformat() if leg else None,
            "scheduled_arrival": leg.scheduled_arrival.isoformat() if leg else None,
        }
