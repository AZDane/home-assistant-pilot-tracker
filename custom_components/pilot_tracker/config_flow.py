"""Config and options flows for Pilot Tracker."""

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, OptionsFlow, OptionsFlowWithConfigEntry
from homeassistant.helpers import selector

from .const import CONF_CALENDAR_ENTITY, DOMAIN
from .providers.base import ScheduleParseError
from .schedule import ScheduleConflictError, ScheduleLimitError


class PilotTrackerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title="Pilot Tracker", data={})

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry):
        return PilotTrackerOptionsFlow(config_entry)


class PilotTrackerOptionsFlow(OptionsFlowWithConfigEntry):
    @property
    def _coordinator(self):
        return self.hass.data[DOMAIN][self.config_entry.entry_id]

    async def async_step_init(self, user_input=None):
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "import_schedule", "view_schedule", "reset_aircraft",
                "configure_calendar", "sync_calendar", "complete_current_leg", "clear_schedule"
            ],
        )

    async def async_step_import_schedule(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                await self._coordinator.async_import_schedule(user_input["schedule_text"])
            except ScheduleParseError:
                errors["base"] = "invalid_schedule"
            except ScheduleConflictError:
                errors["base"] = "schedule_conflict"
            except ScheduleLimitError:
                errors["base"] = "schedule_horizon"
            else:
                return self.async_create_entry(title="", data=dict(self.config_entry.options))
        schema = vol.Schema({
            vol.Required("schedule_text"): selector.TextSelector(
                selector.TextSelectorConfig(multiline=True, type=selector.TextSelectorType.TEXT)
            )
        })
        return self.async_show_form(step_id="import_schedule", data_schema=schema, errors=errors)

    async def async_step_configure_calendar(self, user_input=None):
        if user_input is not None:
            entity_id = user_input.get(CONF_CALENDAR_ENTITY) or None
            await self._coordinator.async_configure_calendar(entity_id)
            return self.async_create_entry(title="", data={CONF_CALENDAR_ENTITY: entity_id})
        current = self.config_entry.options.get(CONF_CALENDAR_ENTITY)
        field = vol.Optional(CONF_CALENDAR_ENTITY, default=current) if current else vol.Optional(CONF_CALENDAR_ENTITY)
        schema = vol.Schema({
            field: selector.EntitySelector(
                selector.EntitySelectorConfig(domain="calendar")
            )
        })
        return self.async_show_form(step_id="configure_calendar", data_schema=schema)

    async def async_step_sync_calendar(self, user_input=None):
        if user_input is not None:
            await self._coordinator.async_sync_calendar()
            return self.async_create_entry(title="", data=dict(self.config_entry.options))
        sync = self._coordinator.calendar_sync
        status = (
            f"Calendar: {sync.entity_id or 'Not configured'}\n"
            f"Last sync: {sync.last_sync.isoformat() if sync.last_sync else 'Never'}\n"
            f"Recognized events: {sync.imported_events}\n"
            f"Last error: {sync.last_error or 'None'}"
        )
        return self.async_show_form(
            step_id="sync_calendar",
            data_schema=vol.Schema({vol.Required("sync_now", default=True): bool}),
            description_placeholders={"calendar_status": status},
        )

    async def async_step_view_schedule(self, user_input=None):
        trips = self._coordinator.store.trips
        if not trips:
            return self.async_show_form(
                step_id="view_schedule", data_schema=vol.Schema({}),
                description_placeholders={"schedule": "No schedules imported."},
            )
        if user_input is None:
            choices = {trip.key: self._trip_label(trip) for trip in trips}
            return self.async_show_form(
                step_id="view_schedule",
                data_schema=vol.Schema({vol.Required("trip_key"): vol.In(choices)}),
                description_placeholders={"schedule": f"{len(trips)} schedule(s) loaded."},
            )
        trip = self._coordinator.store.get(user_input["trip_key"])
        if trip:
            legs = "\n".join(
                f"{leg.sequence}. {leg.date} WN{leg.flight_number} {leg.origin} -> {leg.destination} ({leg.status.value})"
                for leg in trip.legs
            )
            summary = f"Trip {trip.trip_id} - {len(trip.legs)} legs\n{legs}"
        else:
            summary = "No schedule imported."
        return self.async_show_form(
            step_id="view_schedule_detail",
            data_schema=vol.Schema({}),
            description_placeholders={"schedule": summary},
        )

    async def async_step_view_schedule_detail(self, user_input=None):
        return self.async_create_entry(title="", data=dict(self.config_entry.options))

    async def async_step_clear_schedule(self, user_input=None):
        if user_input is not None:
            if user_input["confirm"]:
                try:
                    await self._coordinator.async_remove_schedule(user_input["trip_key"])
                except ValueError:
                    return self.async_show_form(
                        step_id="clear_schedule", data_schema=self._remove_schema(),
                        errors={"base": "active_trip_tracking"},
                    )
                else:
                    return self.async_create_entry(title="", data=dict(self.config_entry.options))
            return await self.async_step_init()
        return self.async_show_form(
            step_id="clear_schedule",
            data_schema=self._remove_schema(),
        )

    def _remove_schema(self):
        choices = {trip.key: self._trip_label(trip) for trip in self._coordinator.store.trips}
        return vol.Schema({
            vol.Required("trip_key"): vol.In(choices),
            vol.Required("confirm", default=False): bool,
        })

    @staticmethod
    def _trip_label(trip):
        return f"{trip.trip_id} ({trip.legs[0].date} to {trip.legs[-1].date}, {len(trip.legs)} legs)"

    async def async_step_reset_aircraft(self, user_input=None):
        errors = {}
        if user_input is not None:
            if not user_input["confirm"]:
                return await self.async_step_init()
            try:
                await self._coordinator.async_reset_current_aircraft()
            except ValueError:
                errors["base"] = "no_current_leg"
            else:
                return self.async_create_entry(title="", data=dict(self.config_entry.options))
        return self.async_show_form(
            step_id="reset_aircraft",
            data_schema=vol.Schema({vol.Required("confirm", default=False): bool}),
            errors=errors,
        )

    async def async_step_complete_current_leg(self, user_input=None):
        errors = {}
        if user_input is not None:
            if not user_input["confirm"]:
                return await self.async_step_init()
            try:
                await self._coordinator.async_manually_complete_current_leg()
            except ValueError:
                errors["base"] = "no_active_leg"
            else:
                return self.async_create_entry(title="", data=dict(self.config_entry.options))
        return self.async_show_form(
            step_id="complete_current_leg",
            data_schema=vol.Schema({vol.Required("confirm", default=False): bool}),
            errors=errors,
        )
