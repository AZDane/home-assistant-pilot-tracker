"""Pilot Tracker integration."""

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CoreState, EVENT_HOMEASSISTANT_STARTED, HomeAssistant
from homeassistant.helpers import config_validation as cv

from .airports import load_airports
from .const import DOMAIN, PLATFORMS
from .coordinator import PilotTrackerCoordinator
from .frontend import FrontendRegistration
from .storage import TripStore

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register services and the bundled dashboard card."""
    async def register_frontend(_event=None):
        await FrontendRegistration(hass).async_register()

    if hass.state == CoreState.running:
        await register_frontend()
    else:
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, register_frontend)

    def coordinator():
        entries = hass.data.get(DOMAIN, {})
        if not entries:
            raise ValueError("Pilot Tracker is not loaded")
        return next(iter(entries.values()))

    async def import_schedule(call):
        await coordinator().async_import_schedule(call.data["schedule_text"])

    async def remove_schedule(call):
        await coordinator().async_remove_schedule(call.data["trip_key"])

    async def resolve_conflict(call):
        await coordinator().async_resolve_schedule_conflict(call.data["keep_trip_key"])

    async def reset_aircraft(call):
        await coordinator().async_reset_current_aircraft()

    async def complete_leg(call):
        await coordinator().async_manually_complete_current_leg()

    hass.services.async_register(
        DOMAIN, "import_schedule", import_schedule,
        schema=vol.Schema({vol.Required("schedule_text"): cv.string}),
    )
    hass.services.async_register(
        DOMAIN, "remove_schedule", remove_schedule,
        schema=vol.Schema({vol.Required("trip_key"): cv.string}),
    )
    hass.services.async_register(
        DOMAIN, "resolve_schedule_conflict", resolve_conflict,
        schema=vol.Schema({vol.Required("keep_trip_key"): cv.string}),
    )
    hass.services.async_register(DOMAIN, "reset_aircraft", reset_aircraft)
    hass.services.async_register(DOMAIN, "complete_current_leg", complete_leg)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # airportsdata reads its bundled CSV on first use. Warm the cache outside
    # the event loop before sensors or schedule parsing can request a lookup.
    await hass.async_add_executor_job(load_airports)
    coordinator = PilotTrackerCoordinator(hass, TripStore(hass))
    await coordinator.async_restore()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await coordinator.async_setup_tracking()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await coordinator.async_shutdown()
        hass.data[DOMAIN].pop(entry.entry_id)
    return unloaded
