"""Runtime coordinator for the Phase 1 entity surface."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from time import monotonic

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .models import FlightLeg, LegStatus, Trip, TripStatus
from .flight_validation import validate_candidate
from .arrival import arrival_signals, event_matches_flight
from .providers.southwest import SouthwestPairingProvider
from .schedule import (
    merge_trip, overlapping_trip_keys, select_pending_leg, trips_overlap,
    validate_collection_horizon, validate_leg_order,
)
from .state_machine import PilotTrackerState
from .storage import TripStore
from .tracking.flightradar24 import FlightRadar24Adapter, FlightRadar24NotReady

_LOGGER = logging.getLogger(__name__)
RESOLVE_BEFORE = timedelta(hours=4)
RESOLVE_AFTER = timedelta(hours=12)
TRACKING_STALE_SECONDS = 120
RETRY_TRACKING_SECONDS = 300


class PilotTrackerCoordinator(DataUpdateCoordinator[None]):
    def __init__(self, hass: HomeAssistant, store: TripStore) -> None:
        super().__init__(hass, logger=_LOGGER, name="pilot_tracker")
        self.store = store
        self.trip: Trip | None = None
        self.state = PilotTrackerState.NO_SCHEDULE
        self.tracking = FlightRadar24Adapter(hass)
        self.tracking_error: str | None = None
        self.accepted_flight: dict | None = None
        self.flight_path: list[tuple[float, float]] = []
        self.last_rejection: str | None = None
        self.last_rejection_detail: dict | None = None
        self._unsub_interval = None
        self._accepted_updated_at = 0.0
        self._last_tracking_request_at = 0.0
        self.schedule_conflicts: list[str] = []

    async def async_restore(self) -> None:
        self.trip = await self.store.async_load()
        if self.trip and self.trip.current_leg and self.trip.current_leg.status != LegStatus.ACTIVE:
            _LOGGER.warning(
                "Clearing stale current-leg pointer %s during startup recovery",
                self.trip.current_leg_sequence,
            )
            self.trip.current_leg_sequence = None
            await self.store.async_save(self.trip)
        self.trip = self._choose_operational_trip(self.trip)
        self.state = PilotTrackerState.WAITING_FOR_DUTY if self.trip else PilotTrackerState.NO_SCHEDULE
        self.async_set_updated_data(None)

    async def async_setup_tracking(self) -> None:
        self._unsub_interval = async_track_time_interval(
            self.hass, self._interval_update, timedelta(seconds=30)
        )
        try:
            await self.tracking.async_setup(self._tracking_updated)
        except FlightRadar24NotReady as error:
            self.tracking_error = str(error)
        else:
            self.tracking_error = None
            await self.async_evaluate_tracking()
        self.async_set_updated_data(None)

    def _tracking_updated(self) -> None:
        self.async_set_updated_data(None)
        self.hass.async_create_task(self.async_evaluate_tracking())

    @callback
    def _interval_update(self, _now: datetime) -> None:
        self.hass.async_create_task(self.async_evaluate_tracking())

    async def async_shutdown(self) -> None:
        if self._unsub_interval:
            self._unsub_interval()
            self._unsub_interval = None
        await self.tracking.async_shutdown()

    async def async_evaluate_tracking(self) -> None:
        """Resolve duty starts and advance later legs only after prior completion."""
        if not self.trip:
            return
        if not self.tracking.available:
            if not self.tracking.refresh_discovery() or not self.tracking.available:
                self.tracking_error = "Flightradar24 entities are unavailable"
                self.async_set_updated_data(None)
                return
            self.tracking_error = None
        leg = self.current_leg
        if leg is None:
            leg = self._select_duty_start()
        if leg is None:
            return
        now = datetime.now(tz=leg.scheduled_departure.tzinfo)
        if leg.status == LegStatus.ACTIVE and leg.tracking_identifiers:
            await self._update_active_leg(leg, now)
            return
        if now < leg.scheduled_departure - RESOLVE_BEFORE:
            self.last_rejection = None
            self.state = (PilotTrackerState.WAITING_FOR_FIRST_LEG
                          if self._is_duty_start(leg) else PilotTrackerState.WAITING_FOR_NEXT_LEG)
            self.async_set_updated_data(None)
            return
        if now > leg.scheduled_arrival + RESOLVE_AFTER and not leg.tracking_identifiers:
            self.state = PilotTrackerState.ERROR
            self.last_rejection = "first_leg_resolution_window_expired"
            self.async_set_updated_data(None)
            return
        self.trip.current_leg_sequence = leg.sequence
        self.state = PilotTrackerState.RESOLVING_FLIGHT
        request_id = f"{leg.airline}{leg.flight_number}"
        if self.trip.metadata.get("tracking_request") != request_id:
            await self.tracking.async_start(request_id)
            self._last_tracking_request_at = monotonic()
            self.trip.metadata["tracking_request"] = request_id
            await self.store.async_save(self.trip)
            _LOGGER.info("Opening resolution window for %s %s->%s", request_id, leg.origin, leg.destination)

        accepted = None
        self.last_rejection = None
        self.last_rejection_detail = None
        for candidate in self.tracking.flights:
            result = validate_candidate(leg, candidate)
            if result.accepted:
                accepted = candidate
                break
            if normalize := candidate.get("flight_number"):
                if leg.flight_number in str(normalize):
                    self.last_rejection = result.reason
                    self.last_rejection_detail = {
                        "flight": f"{leg.airline}{leg.flight_number}",
                        "expected_origin": leg.origin,
                        "received_origin": candidate.get("airport_origin_code_iata"),
                        "expected_destination": leg.destination,
                        "received_destination": candidate.get("airport_destination_code_iata"),
                    }
        if accepted is None:
            self.accepted_flight = None
            self.async_set_updated_data(None)
            return
        if leg.tracking_identifiers:
            identity_keys = ("id", "aircraft_registration", "aircraft_icao_24bit")
            if not any(
                leg.tracking_identifiers.get(key) == str(accepted.get(key))
                for key in identity_keys if leg.tracking_identifiers.get(key) and accepted.get(key)
            ):
                self.accepted_flight = None
                self.last_rejection = "aircraft_identifier_changed"
                self.state = PilotTrackerState.ERROR
                _LOGGER.warning("Candidate for %s rejected: aircraft identifier changed", request_id)
                self.async_set_updated_data(None)
                return
        self.accepted_flight = accepted
        self._remember_position(accepted)
        self._accepted_updated_at = monotonic()
        if leg.status == LegStatus.ACTIVE and leg.tracking_identifiers:
            self.state = PilotTrackerState.TRACKING_FLIGHT
            self.async_set_updated_data(None)
            return
        leg.status = LegStatus.ACTIVE
        leg.tracking_identifiers = {
            key: str(accepted[key]) for key in
            ("id", "aircraft_registration", "callsign", "aircraft_icao_24bit") if accepted.get(key)
        }
        self.state = PilotTrackerState.TRACKING_FLIGHT
        await self.store.async_save(self.trip)
        _LOGGER.info("Current leg %s %s identified; tracking aircraft %s", leg.sequence, request_id,
                     leg.tracking_identifiers.get("aircraft_registration", "unknown"))
        self.async_set_updated_data(None)

    def _is_duty_start(self, leg: FlightLeg) -> bool:
        return not any(item.duty_period == leg.duty_period and item.sequence < leg.sequence for item in self.trip.legs)

    def _select_duty_start(self) -> FlightLeg | None:
        now = datetime.now(tz=self.trip.legs[0].scheduled_departure.tzinfo)
        # When a schedule is imported or HA restarts mid-duty, select the most
        # recently departed leg that can still be operating. If no leg is in
        # that window, wait for the earliest future leg. This prevents pinning
        # tracking to the duty's already-finished first leg.
        selected = select_pending_leg(self.trip, now)
        if selected is None:
            self.last_rejection = None
            self.state = PilotTrackerState.TRIP_COMPLETE
            return None
        if now >= selected.scheduled_departure - RESOLVE_BEFORE:
            for leg in self.trip.legs:
                if leg.sequence < selected.sequence and leg.status == LegStatus.PENDING:
                    leg.status = LegStatus.SKIPPED
            self.trip.current_leg_sequence = selected.sequence
        return selected

    async def _update_active_leg(self, leg: FlightLeg, now: datetime) -> None:
        candidate = next((flight for flight in self.tracking.flights if any(
            leg.tracking_identifiers.get(key) == str(flight.get(key))
            for key in ("id", "aircraft_registration", "aircraft_icao_24bit")
            if leg.tracking_identifiers.get(key) and flight.get(key)
        )), None)
        if candidate:
            self.accepted_flight = candidate
            self._remember_position(candidate)
            self._accepted_updated_at = monotonic()
        else:
            replacement = next((flight for flight in self.tracking.flights
                                if validate_candidate(leg, flight).accepted), None)
            if replacement is not None:
                self.accepted_flight = None
                self.last_rejection = "aircraft_identifier_changed"
                self.state = PilotTrackerState.ERROR
                _LOGGER.warning(
                    "Aircraft swap detected for leg %s: %s -> %s; awaiting confirmation",
                    leg.sequence,
                    leg.tracking_identifiers.get("aircraft_registration", "unknown"),
                    replacement.get("aircraft_registration", "unknown"),
                )
                self.async_set_updated_data(None)
                return
        if candidate is None and monotonic() - self._last_tracking_request_at >= RETRY_TRACKING_SECONDS:
            # Re-query the scheduled flight rather than only the old tail so an
            # FR24-visible aircraft substitution can be detected automatically.
            identifier = self.trip.metadata.get("tracking_request") or f"{leg.airline}{leg.flight_number}"
            await self.tracking.async_start(identifier)
            self._last_tracking_request_at = monotonic()
            _LOGGER.info("Re-requested FR24 tracking for active leg %s using %s", leg.sequence, identifier)
        event = self.tracking.last_event if event_matches_flight(
            self.tracking.last_event, leg.tracking_identifiers
        ) else None
        signals = arrival_signals(self.accepted_flight or {}, event)
        previous_evidence = set(self.trip.metadata.get("arrival_evidence", []))
        previous_samples = int(self.trip.metadata.get("ground_near_samples", 0))
        evidence = previous_evidence | signals
        samples = previous_samples
        if "ground_near_destination" in signals:
            samples += 1
        self.trip.metadata["arrival_evidence"] = sorted(evidence)
        self.trip.metadata["ground_near_samples"] = samples
        if evidence:
            self.state = PilotTrackerState.ARRIVAL_PENDING
        complete = (
            "gate_event" in evidence
            or ("ground_near_destination" in evidence and ("landed_event" in evidence or samples >= 2))
            or (now >= leg.scheduled_arrival + timedelta(hours=2)
                and "ground_near_destination" in evidence)
        )
        if complete:
            await self._complete_leg(leg)
        else:
            if evidence != previous_evidence or samples != previous_samples:
                await self.store.async_save(self.trip)
            self.async_set_updated_data(None)

    async def _complete_leg(self, leg: FlightLeg) -> None:
        leg.status = LegStatus.COMPLETED
        _LOGGER.info("Arrival detected at %s; leg %s marked complete", leg.destination, leg.sequence)
        identifier = self.trip.metadata.pop("tracking_request", None)
        self.trip.metadata.pop("arrival_evidence", None)
        self.trip.metadata.pop("ground_near_samples", None)
        if identifier:
            await self.tracking.async_stop(identifier)
        self.accepted_flight = None
        self.flight_path = []
        next_leg = next((item for item in self.trip.legs if item.sequence > leg.sequence), None)
        if next_leg is None:
            self.trip.current_leg_sequence = None
            self.trip.status = TripStatus.COMPLETE
            await self.store.async_archive(self.trip)
            self.trip = self._choose_operational_trip(None)
            self.state = PilotTrackerState.WAITING_FOR_DUTY if self.trip else PilotTrackerState.TRIP_COMPLETE
            self.async_set_updated_data(None)
            return
        if leg.destination != next_leg.origin:
            self.state = PilotTrackerState.ERROR
            self.last_rejection = "airport_continuity_mismatch"
            await self.store.async_save(self.trip)
            self.async_set_updated_data(None)
            return
        self.trip.current_leg_sequence = next_leg.sequence
        self.state = PilotTrackerState.TURNAROUND
        await self.store.async_save(self.trip)
        self.async_set_updated_data(None)
        await self.async_evaluate_tracking()

    @property
    def tracking_position_fresh(self) -> bool:
        return bool(
            self.accepted_flight
            and monotonic() - self._accepted_updated_at <= TRACKING_STALE_SECONDS
        )

    def _remember_position(self, flight: dict) -> None:
        """Keep a bounded in-memory breadcrumb trail for the independent map."""
        if flight.get("latitude") is None or flight.get("longitude") is None:
            return
        point = (float(flight["latitude"]), float(flight["longitude"]))
        if not self.flight_path or self.flight_path[-1] != point:
            self.flight_path.append(point)
            self.flight_path = self.flight_path[-1000:]

    async def async_import_schedule(self, text: str) -> Trip:
        imported = SouthwestPairingProvider().parse(text)
        validate_leg_order(imported)
        existing = self.store.get(imported.key)
        merged = merge_trip(existing, imported)
        validate_collection_horizon(self.store.trips, merged)
        await self.store.async_save(merged)
        self.trip = self._choose_operational_trip(self.trip)
        self.last_rejection = None
        if self.schedule_conflicts:
            self.last_rejection = "overlapping_schedule_conflict"
            self.state = PilotTrackerState.ERROR
        else:
            self.state = PilotTrackerState.WAITING_FOR_DUTY
        self.async_set_updated_data(None)
        await self.async_evaluate_tracking()
        return merged

    async def async_clear_schedule(self) -> None:
        if self.trip:
            await self.store.async_remove(self.trip.key)
        self.trip = self._choose_operational_trip(None)
        self.state = PilotTrackerState.WAITING_FOR_DUTY if self.trip else PilotTrackerState.NO_SCHEDULE
        self.async_set_updated_data(None)

    async def async_remove_schedule(self, trip_key: str) -> None:
        removing_active = self.trip is not None and self.trip.key == trip_key
        if removing_active and self.current_leg and self.current_leg.status == LegStatus.ACTIVE:
            raise ValueError("Cannot remove a trip while its current leg is being tracked")
        await self.store.async_remove(trip_key)
        # A conflict can leave self.trip unset, so recompute after every
        # deletion—not only when the removed key happened to be selected.
        preferred = None if removing_active else self.trip
        self.trip = self._choose_operational_trip(preferred)
        if self.schedule_conflicts:
            self.last_rejection = "overlapping_schedule_conflict"
            self.state = PilotTrackerState.ERROR
        else:
            if self.last_rejection == "overlapping_schedule_conflict":
                self.last_rejection = None
            active_leg = self.current_leg
            if active_leg and active_leg.status == LegStatus.ACTIVE:
                self.state = PilotTrackerState.TRACKING_FLIGHT
            else:
                self.state = PilotTrackerState.WAITING_FOR_DUTY if self.trip else PilotTrackerState.NO_SCHEDULE
        self.async_set_updated_data(None)
        if self.trip and not self.schedule_conflicts:
            await self.async_evaluate_tracking()

    def _choose_operational_trip(self, preferred: Trip | None) -> Trip | None:
        trips = [trip for trip in self.store.trips if trip.status == TripStatus.ACTIVE]
        self.schedule_conflicts = overlapping_trip_keys(trips)
        if not trips:
            return None
        if self.schedule_conflicts:
            self.last_rejection = "overlapping_schedule_conflict"
            self.state = PilotTrackerState.ERROR
            return None
        # A trip may pin operational ownership only after its current aircraft
        # has been positively identified. Merely being the previously selected
        # future trip must not block a newly eligible earlier pairing.
        if (
            preferred
            and preferred.status == TripStatus.ACTIVE
            and preferred in self.store.trips
            and preferred.current_leg is not None
            and preferred.current_leg.status == LegStatus.ACTIVE
            and bool(preferred.current_leg.tracking_identifiers)
        ):
            self.hass.async_create_task(self.store.async_select(preferred.key))
            return preferred
        now = datetime.now(tz=trips[0].legs[0].scheduled_departure.tzinfo)
        current = [trip for trip in trips if trip.legs[0].scheduled_departure - RESOLVE_BEFORE <= now
                   <= trip.legs[-1].scheduled_arrival + timedelta(hours=4)]
        if len(current) > 1:
            self.schedule_conflicts = [trip.key for trip in current]
            self.last_rejection = "overlapping_schedule_conflict"
            self.state = PilotTrackerState.ERROR
            return None
        selected = current[0] if current else next(
            (trip for trip in trips if trip.legs[-1].scheduled_arrival + timedelta(hours=4) >= now),
            None,
        )
        if selected:
            self.hass.async_create_task(self.store.async_select(selected.key))
        return selected

    async def async_resolve_schedule_conflict(self, keep_trip_key: str) -> None:
        """Keep one explicitly selected conflicting trip and delete its peers."""
        if keep_trip_key not in self.schedule_conflicts:
            raise ValueError("Selected trip is not part of the current conflict")
        kept = self.store.get(keep_trip_key)
        if kept is None:
            raise ValueError("Selected trip no longer exists")
        for trip in list(self.store.trips):
            if trip.key != keep_trip_key and trips_overlap(kept, trip):
                await self.store.async_remove(trip.key)
        self.schedule_conflicts = overlapping_trip_keys(self.store.trips)
        self.last_rejection = None
        self.trip = kept
        await self.store.async_select(kept.key)
        self.state = PilotTrackerState.ERROR if self.schedule_conflicts else PilotTrackerState.WAITING_FOR_DUTY
        if self.schedule_conflicts:
            self.last_rejection = "overlapping_schedule_conflict"
        self.async_set_updated_data(None)
        await self.async_evaluate_tracking()

    async def async_reset_current_aircraft(self) -> None:
        """Explicitly release an accepted aircraft so a swap can be resolved."""
        leg = self.current_leg
        if leg is None:
            raise ValueError("There is no current leg")
        identifier = (
            leg.tracking_identifiers.get("aircraft_registration")
            or self.trip.metadata.get("tracking_request")
        )
        if identifier:
            await self.tracking.async_stop(identifier)
        leg.tracking_identifiers = {}
        leg.status = LegStatus.PENDING
        self.trip.metadata.pop("tracking_request", None)
        self.trip.metadata.pop("arrival_evidence", None)
        self.trip.metadata.pop("ground_near_samples", None)
        self.accepted_flight = None
        self.flight_path = []
        self.last_rejection = None
        self.state = PilotTrackerState.RESOLVING_FLIGHT
        await self.store.async_save(self.trip)
        self.async_set_updated_data(None)
        await self.async_evaluate_tracking()

    async def async_manually_complete_current_leg(self) -> None:
        """Explicit recovery for an arrival that automatic evidence missed."""
        leg = self.current_leg
        if leg is None or leg.status != LegStatus.ACTIVE:
            raise ValueError("There is no active leg to complete")
        _LOGGER.warning("Current leg %s manually marked complete", leg.sequence)
        await self._complete_leg(leg)

    @property
    def current_leg(self) -> FlightLeg | None:
        return self.trip.current_leg if self.trip else None

    @property
    def next_leg(self) -> FlightLeg | None:
        if not self.trip:
            return None
        pointed = self.trip.current_leg
        # Completion advances the pointer to the following pending leg. That
        # leg is the next flight; searching only after the pointer skips it.
        if pointed and pointed.status == LegStatus.PENDING:
            return pointed
        current = self.trip.current_leg_sequence or 0
        candidates = [
            leg for leg in self.trip.legs
            if leg.sequence > current and leg.status == LegStatus.PENDING
        ]
        if current:
            return candidates[0] if candidates else None
        now = datetime.now(tz=self.trip.legs[0].scheduled_departure.tzinfo)
        for leg in candidates:
            duty_end = max(
                item.scheduled_arrival for item in self.trip.legs if item.duty_period == leg.duty_period
            )
            if self._is_duty_start(leg) and now <= duty_end + timedelta(hours=4):
                return leg
        return None
