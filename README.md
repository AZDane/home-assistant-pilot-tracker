# Pilot Tracker for Home Assistant

[![Open Pilot Tracker in the Home Assistant Marketplace](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=AZDane&repository=home-assistant-pilot-tracker&category=integration)

Pilot Tracker imports airline pairing text, identifies the scheduled live flight
through an existing FlightRadar24 integration, and exposes the pilot's aircraft
position and schedule as Home Assistant entities and dashboard cards.

## Features

- Imports multiple pairings covering up to 62 days.
- Supports compact PDF text and tabular print-view text.
- Understands Herb, Local, and Domicile schedule time modes.
- Recognizes `DV` diversion segments and same-number continuation legs.
- Shows the current CrewHub layover hotel and phone number during the layover.
- Tracks the positively identified aircraft through FlightRadar24.
- Exposes current and next flight, route, status, diagnostics, and
  `device_tracker.pilot` entities.
- Includes full Pilot Tracker and map-only dashboard cards.
- Uses a self-contained bright map with a heading-aware aircraft marker and
  live breadcrumb trail.
- Preserves a manually selected map zoom during position updates.

## Schedule time modes

The parser reads the time mode printed in the pairing:

- **Herb Time**: all schedule times use `America/Chicago`.
- **Domicile Time**: all schedule times use the IANA time zone of the first
  leg's origin airport.
- **Local Time**: each departure uses the origin airport's time zone and each
  arrival uses the destination airport's time zone.

Airport time zones are resolved from the `airportsdata` IATA database. An
unknown or non-airport IATA code produces an import error instead of silently
using the wrong time zone.

## Requirements

- Home Assistant 2026.11 or newer with the built-in
  [Marketplace](https://www.home-assistant.io/integrations/marketplace/), or
  HACS on an older Home Assistant release.
- The
  [FlightRadar24 custom integration](https://github.com/AlexandrErohin/home-assistant-flightradar24)
  configured in Home Assistant.

Pilot Tracker does not require the separately published
`home-assistant-flightradar24-card` frontend repository. The FlightRadar24
integration already bundles its own card with the same custom-element name, so
installing both FlightRadar24 card implementations can cause a duplicate
registration error in the browser.

## Install from the Home Assistant Marketplace

1. Open **Settings → Marketplace**.
2. In the upper-right corner, select **Add from link**.
3. Add `https://github.com/AZDane/home-assistant-pilot-tracker`.
4. Open **Pilot Tracker** in the Marketplace and select **Install**.
5. If setup does not open automatically, go to **Settings → Devices & services
   → Add integration** and search for **Pilot Tracker**.

Home Assistant 2026.11 automatically migrates existing HACS installations and
custom repositories to the Marketplace. On Home Assistant 2026.10 or earlier,
use the equivalent **HACS → Custom repositories** flow and add this repository
as an **Integration**.

## Recommended setup: Home Assistant calendar / CrewHub synchronization

Calendar synchronization is the primary and recommended import method because
CrewHub can revise flight times, routes, hotels, and other trip details without
another manual paste. Pilot Tracker works with any compatible calendar already
exposed to Home Assistant as a `calendar.*` entity, including Google Calendar
and Apple Calendar through the Apple iCloud or CalDAV integration. If your
CrewHub calendar is already synced to Apple Calendar and appears in Home
Assistant, no additional calendar-provider setup is needed in Pilot Tracker.

Add `type: custom:pilot-tracker-card` to a dashboard and select the CrewHub
`calendar.*` entity in **Schedule Manager**. The calendar events must include
the CrewHub pairing details in their descriptions. The selection is saved and
remains active after restarts. The same selection remains available under
**Settings → Devices & services → Pilot Tracker → Configure → Configure
calendar import**.

Pilot Tracker uses Home Assistant's calendar API and never receives or stores
calendar-provider credentials directly.

Recognized CrewHub trip events are imported automatically. Pilot Tracker
synchronizes every five minutes and whenever the calendar entity updates.
Revisions update the matching pairing while preserving completed or active
flight progress. Non-flight calendar events are ignored, and equivalent
calendar and pasted schedules are consolidated automatically.
Expired calendar-managed schedules are archived before newer events are
validated, including schedules older than the calendar query window. Manual
imports and any trip with active aircraft tracking are never removed by this
automatic cleanup. Temporary calendar failures preserve all current and future
schedules and retry on the normal five-minute interval.
The dashboard Schedule Manager labels every entry as **Calendar** or **Pasted**;
expanded calendar entries also show the originating calendar event summary.
When a calendar event matches a pasted pairing, the calendar version becomes
authoritative and future event revisions update that schedule in place.
When CrewHub supplies a layover hotel and telephone number, both dashboard
cards display it from the preceding duty's arrival until the next report time.

### Calendar compatibility and troubleshooting

- Confirm the calendar appears in Home Assistant as a `calendar.*` entity before
  selecting it in Pilot Tracker. For Apple Calendar, configure either the
  [Apple iCloud integration](https://www.home-assistant.io/integrations/icloud/)
  or [CalDAV](https://www.home-assistant.io/integrations/caldav/).
- Apple iCloud calendar entities are read-only in Home Assistant. That does not
  prevent Pilot Tracker from importing their events.
- Open a CrewHub event in the source calendar and confirm its description still
  contains `LOCAL`, dated duty headings such as `Thu Nov 12`, and the flight
  rows. Pilot Tracker ignores ordinary calendar events and cannot reconstruct a
  pairing when the calendar provider omits its description.
- In **Schedule Manager**, select the exact calendar containing the CrewHub
  events, not a similarly named personal or shared calendar.
- To diagnose a missed import, open **Settings → Devices & services → Pilot
  Tracker → Configure → Synchronize calendar now**. The result shows the
  selected entity, last synchronization time, recognized event count, and the
  most recent error.
- If the Apple iCloud integration does not expose the expected calendar, try
  CalDAV with the iCloud server URL. Apple may require an app-specific password,
  as described in the Home Assistant CalDAV documentation.

## Optional manual import

Manual paste remains available as a fallback for schedules that are not in the
calendar. In the Pilot Tracker card, choose **Import schedule** and paste either:

- Full pairing-detail text, including the heading that identifies Herb Time,
  Local Time, or Domicile Time.
- Compact local-time roster text. Every departure and arrival is interpreted
  in the corresponding airport's local time.

A compact single pairing is identified by its first date. A multi-pairing
monthly roster is identified by its dominant month, including a carry-in leg
from the preceding month. When a matching CrewHub calendar event exists, the
calendar-managed schedule becomes authoritative over the pasted copy.

Both normally formatted and flattened calendar descriptions are supported.
Pilot Tracker understands common operational changes—including deadheads,
gate returns, delayed reports, diversions, aircraft changes, and continuation
segments—without treating non-airborne records as live flights.

The integration stores imported schedules in Home Assistant's local storage.
It does not send pairing text or crew information to an external service. Only
the scheduled flight identifier is passed to the already configured
FlightRadar24 integration for tracking.

## Dashboard cards

Full schedule and administration card:

```yaml
type: custom:pilot-tracker-card
```

Map-only card with the next-flight fallback:

```yaml
type: custom:pilot-tracker-map-card
```

## Support and status

This is a community custom integration and is not affiliated with Southwest
Airlines, FlightRadar24, or Home Assistant. Report problems through
[GitHub Issues](https://github.com/AZDane/home-assistant-pilot-tracker/issues).
