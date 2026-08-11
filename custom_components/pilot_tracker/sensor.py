"""Pilot Tracker status sensors."""

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        StatusSensor(coordinator), ScheduleStatusSensor(coordinator), TrackingSourceSensor(coordinator),
        TripSensor(coordinator), CurrentFlightSensor(coordinator), CurrentOriginSensor(coordinator),
        CurrentDestinationSensor(coordinator), NextFlightSensor(coordinator), NextOriginSensor(coordinator),
        NextDestinationSensor(coordinator), FlightMapSensor(coordinator)
    ])


class BaseSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True


class StatusSensor(BaseSensor):
    _attr_name = "Status"
    _attr_unique_id = "pilot_tracker_status"

    @property
    def native_value(self):
        return self.coordinator.state.value


class ScheduleStatusSensor(BaseSensor):
    _attr_name = "Schedule status"
    _attr_unique_id = "pilot_tracker_schedule_status"

    @property
    def native_value(self):
        count = len(self.coordinator.store.trips)
        return "not_loaded" if count == 0 else f"{count}_loaded"


class TrackingSourceSensor(BaseSensor):
    _attr_name = "Tracking source"
    _attr_unique_id = "pilot_tracker_tracking_source"

    @property
    def native_value(self):
        if self.coordinator.tracking_error:
            return "not_ready"
        return "flightradar24" if self.coordinator.tracking.available else "unavailable"

    @property
    def extra_state_attributes(self):
        adapter = self.coordinator.tracking
        entities = adapter.entities
        return {
            "error": self.coordinator.tracking_error,
            "tracked_flights": len(adapter.flights),
            "fr24_config_entry_id": entities.config_entry_id if entities else None,
            "last_event": adapter.last_event,
            "last_candidate_rejection": self.coordinator.last_rejection,
            "position_fresh": self.coordinator.tracking_position_fresh,
        }


class TripSensor(BaseSensor):
    _attr_name = "Trip"
    _attr_unique_id = "pilot_tracker_trip"

    @property
    def native_value(self):
        return self.coordinator.trip.trip_id if self.coordinator.trip else "none"

    @property
    def extra_state_attributes(self):
        trip = self.coordinator.trip
        return ({
            "source": trip.source,
            "schedule_time_mode": trip.metadata.get("schedule_time_mode", "herb"),
            "time_basis": trip.time_basis,
            "leg_count": len(trip.legs),
            "revision_date": trip.revision_date,
            "archived_trip_count": self.coordinator.store.archived_count,
            "loaded_trip_count": len(self.coordinator.store.trips),
            "schedules": [
                {
                    "key": item.key,
                    "trip_id": item.trip_id,
                    "start": item.legs[0].date,
                    "end": item.legs[-1].date,
                    "leg_count": len(item.legs),
                    "selected": bool(trip and item.key == trip.key),
                    "conflicting": item.key in self.coordinator.schedule_conflicts,
                }
                for item in self.coordinator.store.trips
            ],
        } if trip else {
            "archived_trip_count": self.coordinator.store.archived_count,
            "loaded_trip_count": len(self.coordinator.store.trips),
            "schedules": [
                {
                    "key": item.key,
                    "trip_id": item.trip_id,
                    "start": item.legs[0].date,
                    "end": item.legs[-1].date,
                    "leg_count": len(item.legs),
                    "selected": False,
                    "conflicting": item.key in self.coordinator.schedule_conflicts,
                }
                for item in self.coordinator.store.trips
            ],
        })


class CurrentFlightSensor(BaseSensor):
    _attr_name = "Current flight"
    _attr_unique_id = "pilot_tracker_current_flight"

    @property
    def native_value(self):
        leg = self.coordinator.current_leg
        return f"{leg.airline}{leg.flight_number}" if leg else "none"

    @property
    def extra_state_attributes(self):
        return _leg_attributes(self.coordinator.current_leg)


class NextFlightSensor(BaseSensor):
    _attr_name = "Next flight"
    _attr_unique_id = "pilot_tracker_next_flight"

    @property
    def native_value(self):
        leg = self.coordinator.next_leg
        return f"{leg.airline}{leg.flight_number}" if leg else "none"

    @property
    def extra_state_attributes(self):
        return _leg_attributes(self.coordinator.next_leg)


class CurrentOriginSensor(BaseSensor):
    _attr_name = "Current origin"
    _attr_unique_id = "pilot_tracker_current_origin"

    @property
    def native_value(self):
        leg = self.coordinator.current_leg
        return leg.origin if leg else "none"


class CurrentDestinationSensor(BaseSensor):
    _attr_name = "Current destination"
    _attr_unique_id = "pilot_tracker_current_destination"

    @property
    def native_value(self):
        leg = self.coordinator.current_leg
        return leg.destination if leg else "none"


class NextOriginSensor(BaseSensor):
    _attr_name = "Next origin"
    _attr_unique_id = "pilot_tracker_next_origin"

    @property
    def native_value(self):
        leg = self.coordinator.next_leg
        return leg.origin if leg else "none"


class NextDestinationSensor(BaseSensor):
    _attr_name = "Next destination"
    _attr_unique_id = "pilot_tracker_next_destination"

    @property
    def native_value(self):
        leg = self.coordinator.next_leg
        return leg.destination if leg else "none"


class FlightMapSensor(BaseSensor):
    """FR24-card-compatible view of the one positively identified flight."""

    _attr_name = "Pilot Tracker flight map"
    _attr_unique_id = "pilot_tracker_flight_map"
    _attr_icon = "mdi:map-marker-path"
    _unrecorded_attributes = frozenset({"flights"})

    @property
    def native_value(self):
        return 1 if self.coordinator.accepted_flight else 0

    @property
    def extra_state_attributes(self):
        flight = self.coordinator.accepted_flight
        if not flight or flight.get("latitude") is None or flight.get("longitude") is None:
            return {"flights": [], "bounds": None}
        latitude = float(flight["latitude"])
        longitude = float(flight["longitude"])
        # A broad, approximately square regional viewport. The FR24 card uses
        # this boundary both for its initial zoom and its bright OSM basemap.
        latitude_radius = 5.0
        longitude_radius = 6.0
        bounds = (
            f"{latitude + latitude_radius},{latitude - latitude_radius},"
            f"{longitude - longitude_radius},{longitude + longitude_radius}"
        )
        return {"flights": [dict(flight)], "bounds": bounds}


def _leg_attributes(leg):
    if leg is None:
        return {}
    return {
        "origin": leg.origin,
        "destination": leg.destination,
        "scheduled_departure": leg.scheduled_departure.isoformat(),
        "scheduled_arrival": leg.scheduled_arrival.isoformat(),
        "duty_period": leg.duty_period,
        "qualifier": leg.qualifier,
    }
