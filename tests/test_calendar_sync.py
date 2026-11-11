"""Calendar synchronization ordering and retry regression tests."""

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from custom_components.pilot_tracker import calendar_sync as calendar_sync_module
from custom_components.pilot_tracker.calendar_sync import CalendarScheduleSync
from custom_components.pilot_tracker.models import FlightLeg, Trip
from custom_components.pilot_tracker.schedule import validate_collection_horizon

ZONE = ZoneInfo("America/Phoenix")
NOW = datetime(2026, 11, 9, 8, tzinfo=ZONE)


def _trip(day, identifier):
    departure = datetime(2026, 11, day, 10, tzinfo=ZONE)
    return Trip(
        identifier,
        "crewhub_calendar",
        "airport_local",
        [FlightLeg(1, departure.date().isoformat(), "123", "WN", "PHX", "LAS", departure, departure.replace(hour=11))],
        metadata={"calendar_entity_id": "calendar.house"},
    )


class _Services:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    async def async_call(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error:
            raise self.error
        return self.response


class _Hass:
    def __init__(self, response=None, error=None):
        self.services = _Services(response, error)
        self.created_tasks = []

    def async_create_task(self, coroutine):
        self.created_tasks.append(coroutine)


def _event(day):
    return {
        "start": f"2026-11-{day:02d}T09:00:00-07:00",
        "summary": f"Trip {day}",
        "description": f"LOCAL\nThu Nov {day}\n123 PHX 10:00 MST LAS 11:00 PST",
    }


def test_cleanup_runs_before_import_and_horizon_validation(monkeypatch):
    loaded = [_trip(1, "EXPIRED")]
    order = []

    async def cleanup(entity_id, now):
        order.append("cleanup")
        loaded.clear()
        return 1

    async def import_trip(trip):
        order.append("import")
        validate_collection_horizon(loaded, trip)
        loaded.append(trip)
        return trip

    async def reconcile(*args):
        order.append("reconcile")
        return 0

    response = {"calendar.house": {"events": [_event(12), _event(19), _event(25)]}}
    sync = CalendarScheduleSync(_Hass(response), import_trip, lambda key: None, cleanup, reconcile)
    sync.entity_id = "calendar.house"
    monkeypatch.setattr(calendar_sync_module.dt_util, "now", lambda: NOW)
    monkeypatch.setattr(
        calendar_sync_module.CrewHubCalendarProvider,
        "parse",
        lambda self, text, anchor_date: _trip(anchor_date.day, f"NOV{anchor_date.day}"),
    )

    asyncio.run(sync.async_sync())

    assert order == ["cleanup", "import", "import", "import", "reconcile"]
    assert sync.last_error is None
    assert [trip.trip_id for trip in loaded] == ["NOV12", "NOV19", "NOV25"]


def test_apple_icloud_calendar_entity_imports_html_crewhub_event(monkeypatch):
    """An iCloud-backed calendar works through HA's generic calendar action."""
    entity_id = "calendar.icloud_crew_schedule"
    event = {
        "start": "2026-11-12T09:00:00-07:00",
        "end": "2026-11-12T12:00:00-07:00",
        "summary": "CrewHub trip",
        "description": (
            "LOCAL<br>Thu Nov 12<br>Report 09:00 MST<br>"
            "123 PHX 10:00 MST LAS 11:00 PST<br>"
            "Synced from CrewHub BYO"
        ),
    }
    hass = _Hass({entity_id: {"events": [event]}})
    imported = []
    reconciled = []

    async def import_trip(trip):
        imported.append(trip)
        return trip

    async def cleanup(*args):
        return 0

    async def reconcile(*args):
        reconciled.append(args)
        return 0

    sync = CalendarScheduleSync(hass, import_trip, lambda key: None, cleanup, reconcile)
    sync.entity_id = entity_id
    monkeypatch.setattr(calendar_sync_module.dt_util, "now", lambda: NOW)

    asyncio.run(sync.async_sync())

    assert len(imported) == 1
    assert imported[0].trip_id == "CAL-2026-11-12"
    assert imported[0].metadata["calendar_entity_id"] == entity_id
    assert imported[0].metadata["calendar_summary"] == "CrewHub trip"
    assert imported[0].legs[0].origin == "PHX"
    assert imported[0].legs[0].destination == "LAS"
    assert sync.imported_events == 1
    assert sync.last_error is None
    assert reconciled
    args, kwargs = hass.services.calls[0]
    assert args[:2] == ("calendar", "get_events")
    assert kwargs["target"] == {"entity_id": entity_id}
    assert kwargs["return_response"] is True


def test_unavailable_calendar_does_not_run_cleanup():
    cleanup_calls = []

    async def cleanup(*args):
        cleanup_calls.append(args)
        return 0

    async def unused(*args):
        return 0

    sync = CalendarScheduleSync(
        _Hass(error=RuntimeError("Sync from server has not completed")),
        unused,
        lambda key: None,
        cleanup,
        unused,
    )
    sync.entity_id = "calendar.house"

    asyncio.run(sync.async_sync())

    assert cleanup_calls == []
    assert sync.last_error == "Sync from server has not completed"
    assert sync._retry_on_interval_only


def test_malformed_event_allows_safe_cleanup_but_not_reconciliation(monkeypatch):
    calls = []

    async def cleanup(*args):
        calls.append("cleanup")
        return 1

    async def reconcile(*args):
        calls.append("reconcile")
        return 0

    async def unused(*args):
        return 0

    malformed = {
        "start": "2026-11-12T09:00:00-07:00",
        "description": "LOCAL\nThu Nov 12\nNo flight legs",
    }
    response = {"calendar.house": {"events": [malformed]}}
    sync = CalendarScheduleSync(_Hass(response), unused, lambda key: None, cleanup, reconcile)
    sync.entity_id = "calendar.house"
    monkeypatch.setattr(calendar_sync_module.dt_util, "now", lambda: NOW)

    asyncio.run(sync.async_sync())

    assert calls == ["cleanup"]
    assert sync.last_error == "No CrewHub flight legs found"


def test_repeated_identical_warning_is_logged_once(caplog):
    sync = object.__new__(CalendarScheduleSync)
    sync._logged_warning_signatures = set()

    with caplog.at_level(logging.WARNING):
        sync._log_warning_once("same", "Repeated failure: %s", "problem")
        sync._log_warning_once("same", "Repeated failure: %s", "problem")
        sync._log_warning_once("changed", "Repeated failure: %s", "different")

    assert [record.message for record in caplog.records] == [
        "Repeated failure: problem",
        "Repeated failure: different",
    ]


def test_failed_sync_ignores_calendar_state_retry_until_interval():
    sync = object.__new__(CalendarScheduleSync)
    sync.entity_id = "calendar.house"
    sync._retry_on_interval_only = True
    sync._last_sync_attempt = 0.0
    sync.hass = _Hass()
    event = type("Event", (), {"data": {"entity_id": "calendar.house"}})()

    sync._async_state_changed(event)

    assert sync.hass.created_tasks == []
