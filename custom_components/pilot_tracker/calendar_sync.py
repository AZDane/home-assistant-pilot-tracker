"""Synchronize CrewHub pairing descriptions from a Home Assistant calendar."""

from __future__ import annotations

from datetime import date, datetime, timedelta
import hashlib
import logging
import re
from typing import Any, Awaitable, Callable

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .providers.base import ScheduleParseError
from .providers.crewhub import CrewHubCalendarProvider

_LOGGER = logging.getLogger(__name__)
SYNC_INTERVAL = timedelta(minutes=5)
SYNC_PAST = timedelta(days=2)
SYNC_FUTURE = timedelta(days=60)


class CalendarScheduleSync:
    """Read recognized CrewHub events through HA's calendar action."""

    def __init__(
        self,
        hass: HomeAssistant,
        import_trip: Callable[[Any], Awaitable[Any]],
        get_trip: Callable[[str], Any | None],
    ) -> None:
        self.hass = hass
        self._import_trip = import_trip
        self._get_trip = get_trip
        self.entity_id: str | None = None
        self.last_sync: datetime | None = None
        self.last_error: str | None = None
        self.imported_events = 0
        self._unsub_interval = None
        self._unsub_state = None
        self._syncing = False

    async def async_configure(self, entity_id: str | None) -> None:
        self.async_shutdown()
        self.entity_id = entity_id
        if not entity_id:
            return
        self._unsub_interval = async_track_time_interval(
            self.hass, self._async_interval, SYNC_INTERVAL
        )
        self._unsub_state = self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._async_state_changed)
        await self.async_sync()

    @callback
    def _async_interval(self, _now: datetime) -> None:
        self.hass.async_create_task(self.async_sync())

    @callback
    def _async_state_changed(self, event: Event) -> None:
        if event.data.get("entity_id") == self.entity_id:
            self.hass.async_create_task(self.async_sync())

    async def async_sync(self) -> None:
        if not self.entity_id or self._syncing:
            return
        self._syncing = True
        try:
            now = dt_util.now()
            response = await self.hass.services.async_call(
                "calendar", "get_events",
                {
                    "start_date_time": (now - SYNC_PAST).isoformat(),
                    "end_date_time": (now + SYNC_FUTURE).isoformat(),
                },
                target={"entity_id": self.entity_id},
                blocking=True,
                return_response=True,
            )
            events = ((response or {}).get(self.entity_id) or {}).get("events", [])
            recognized = 0
            had_error = False
            for event in events:
                description = event.get("description") or ""
                if not self._looks_like_crewhub(description):
                    continue
                recognized += 1
                anchor = self._event_date(event.get("start"), now.date())
                try:
                    trip = CrewHubCalendarProvider().parse(
                        description, anchor_date=anchor
                    )
                    legacy_id = f"CAL-{trip.legs[0].date}"
                    legacy_key = f"{legacy_id}:{trip.legs[0].date}"
                    if self._get_trip(legacy_key) or trip.trip_id == trip.legs[0].date:
                        # Preserve the key created by v1.5.0 so upgrading does
                        # not duplicate an already synchronized pairing.
                        trip.trip_id = legacy_id
                    fingerprint = hashlib.sha256(
                        b"crewhub-v2\0" + html_text(description).encode()
                    ).hexdigest()
                    trip.metadata.update({
                        "calendar_entity_id": self.entity_id,
                        "calendar_summary": event.get("summary") or "",
                        "calendar_fingerprint": fingerprint,
                    })
                    existing = self._get_trip(trip.key)
                    if trip.legs[-1].scheduled_arrival < now and existing is None:
                        continue
                    if existing and existing.metadata.get("calendar_fingerprint") == fingerprint:
                        continue
                    await self._import_trip(trip)
                except (ScheduleParseError, ValueError) as error:
                    _LOGGER.warning("Could not import CrewHub calendar event on %s: %s", anchor, error)
                    self.last_error = str(error)
                    had_error = True
                    continue
            self.imported_events = recognized
            self.last_sync = now
            if not had_error:
                self.last_error = None
        except Exception as error:  # Calendar providers surface integration-specific errors.
            self.last_error = str(error)
            _LOGGER.warning("Pilot Tracker calendar synchronization failed: %s", error)
        finally:
            self._syncing = False

    @staticmethod
    def _looks_like_crewhub(description: str) -> bool:
        return bool(
            re.search(r"(?i)\bLOCAL\b", html_text(description))
            and re.search(r"(?i)\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+[A-Z][a-z]{2}\s+\d{1,2}\b", html_text(description))
        )

    @staticmethod
    def _event_date(value: Any, fallback: date) -> date:
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return dt_util.as_local(parsed).date() if parsed.tzinfo else parsed.date()
            except ValueError:
                try:
                    return date.fromisoformat(value)
                except ValueError:
                    pass
        return fallback

    def async_shutdown(self) -> None:
        if self._unsub_interval:
            self._unsub_interval()
            self._unsub_interval = None
        if self._unsub_state:
            self._unsub_state()
            self._unsub_state = None


def html_text(value: str) -> str:
    """Normalize escaped whitespace enough for format recognition."""
    import html

    normalized = html.unescape(value).replace("\xa0", " ")
    normalized = re.sub(r"(?i)<br\s*/?>", "\n", normalized)
    return re.sub(r"<[^>]+>", "", normalized)
