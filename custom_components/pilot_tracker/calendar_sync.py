"""Synchronize CrewHub pairing descriptions from a Home Assistant calendar."""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from time import monotonic
from typing import Any

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
STATE_CHANGE_DEBOUNCE_SECONDS = 30


class CalendarScheduleSync:
    """Read recognized CrewHub events through HA's calendar action."""

    def __init__(
        self,
        hass: HomeAssistant,
        import_trip: Callable[[Any], Awaitable[Any]],
        get_trip: Callable[[str], Any | None],
        cleanup_expired_trips: Callable[[str, datetime], Awaitable[int]],
        reconcile_trips: Callable[
            [str, set[str], datetime, datetime], Awaitable[int]
        ],
    ) -> None:
        self.hass = hass
        self._import_trip = import_trip
        self._get_trip = get_trip
        self._cleanup_expired_trips = cleanup_expired_trips
        self._reconcile_trips = reconcile_trips
        self.entity_id: str | None = None
        self.last_sync: datetime | None = None
        self.last_error: str | None = None
        self.imported_events = 0
        self._unsub_interval = None
        self._unsub_state = None
        self._syncing = False
        self._last_sync_attempt = 0.0
        self._retry_on_interval_only = False
        self._logged_warning_signatures: set[str] = set()

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
        if (
            event.data.get("entity_id") == self.entity_id
            and not self._retry_on_interval_only
            and monotonic() - self._last_sync_attempt >= STATE_CHANGE_DEBOUNCE_SECONDS
        ):
            self.hass.async_create_task(self.async_sync())

    async def async_sync(self) -> None:
        if not self.entity_id or self._syncing:
            return
        self._syncing = True
        self._last_sync_attempt = monotonic()
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
            calendar_response = (response or {}).get(self.entity_id)
            if not isinstance(calendar_response, dict) or not isinstance(
                calendar_response.get("events"), list
            ):
                raise ValueError(
                    f"Calendar response for {self.entity_id} did not contain an event list"
                )
            events = calendar_response["events"]
            # Expired calendar trips must leave the operational collection
            # before new events are horizon-validated. This cleanup is safe
            # after a structurally valid response even if one new event later
            # fails to parse; active tracking and manual imports are protected
            # by the coordinator's cleanup policy.
            await self._cleanup_expired_trips(self.entity_id, now)
            recognized = 0
            had_error = False
            seen_trip_keys: set[str] = set()
            for event in events:
                description = event.get("description") or ""
                if not self._looks_like_crewhub(description):
                    continue
                recognized += 1
                anchor = self._event_date(event.get("start"), now.date())
                fingerprint = hashlib.sha256(
                    b"crewhub-v4\0" + html_text(description).encode()
                ).hexdigest()
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
                    trip.metadata.update({
                        "calendar_entity_id": self.entity_id,
                        "calendar_summary": event.get("summary") or "",
                        "calendar_fingerprint": fingerprint,
                    })
                    existing = self._get_trip(trip.key)
                    if trip.legs[-1].scheduled_arrival < now and existing is None:
                        continue
                    if (
                        existing
                        and existing.source == "crewhub_calendar"
                        and existing.metadata.get("calendar_fingerprint") == fingerprint
                    ):
                        seen_trip_keys.add(existing.key)
                        continue
                    imported = await self._import_trip(trip)
                    seen_trip_keys.add(imported.key)
                except (ScheduleParseError, ValueError) as error:
                    self._log_warning_once(
                        f"event:{fingerprint}:{type(error).__name__}:{error}",
                        "Could not import CrewHub calendar event on %s: %s",
                        anchor,
                        error,
                    )
                    self.last_error = str(error)
                    had_error = True
                    continue
            # A complete calendar response is authoritative for this window.
            # Reconcile only after every recognized event parsed successfully;
            # a malformed event must never cause its previously stored trip to
            # be mistaken for a deletion.
            if not had_error:
                await self._reconcile_trips(
                    self.entity_id,
                    seen_trip_keys,
                    now - SYNC_PAST,
                    now + SYNC_FUTURE,
                )
            self.imported_events = recognized
            self.last_sync = now
            if not had_error:
                self.last_error = None
                self._retry_on_interval_only = False
                self._logged_warning_signatures.clear()
            else:
                # A persistent malformed event should retry on the normal
                # interval, not once per calendar entity state notification.
                self._retry_on_interval_only = True
        except Exception as error:  # Calendar providers surface integration-specific errors.
            self.last_error = str(error)
            self._retry_on_interval_only = True
            self._log_warning_once(
                f"sync:{type(error).__name__}:{error}",
                "Pilot Tracker calendar synchronization failed: %s",
                error,
            )
        finally:
            self._syncing = False

    def _log_warning_once(self, signature: str, message: str, *args: Any) -> None:
        """Log one warning per distinct failure during a failure episode."""
        if signature in self._logged_warning_signatures:
            return
        self._logged_warning_signatures.add(signature)
        _LOGGER.warning(message, *args)

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
