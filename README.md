# Pilot Tracker for Home Assistant

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

- Home Assistant with [HACS](https://hacs.xyz/) installed.
- The
  [FlightRadar24 custom integration](https://github.com/AlexandrErohin/home-assistant-flightradar24)
  configured in Home Assistant.

Pilot Tracker does not require the separately published
`home-assistant-flightradar24-card` HACS frontend repository. The FlightRadar24
integration already bundles its own card with the same custom-element name, so
installing both FlightRadar24 card implementations can cause a duplicate
registration error in the browser.

## Install with HACS

1. In HACS, open **Custom repositories**.
2. Add `https://github.com/AZDane/home-assistant-pilot-tracker` as an
   **Integration** repository.
3. Download **Pilot Tracker** and restart Home Assistant.
4. Open **Settings → Devices & services → Add integration**, then search for
   **Pilot Tracker**.

## Recommended setup: Google Calendar / CrewHub synchronization

Google Calendar is the primary and recommended import method because CrewHub
can revise flight times, routes, hotels, and other trip details without another
manual paste. First configure Google Calendar in Home Assistant. Then add
`type: custom:pilot-tracker-card` to a dashboard and select the CrewHub
`calendar.*` entity in **Schedule Manager**. The selection is saved and remains
active after restarts. The same selection remains available under **Settings →
Devices & services → Pilot Tracker → Configure → Configure calendar import**.

Pilot Tracker uses Home Assistant's calendar API and never receives or stores
Google credentials directly.

Recognized CrewHub descriptions begin with `LOCAL`, contain dated duty headings
such as `Thu Sep 10`, and flight rows such as
`2327 PHX 16:05 MST LAX 17:30 PDT`. Pilot Tracker synchronizes every five
minutes and whenever the calendar entity updates. Revisions update the same
pairing. Removing a calendar event does not automatically delete a stored or
actively tracked trip; remove it explicitly from Pilot Tracker if needed.
Exact duplicates stored under both an older `CAL-YYYY-MM-DD` identifier and a
CrewHub pairing identifier are consolidated automatically. The pairing
identifier and any completed or actively tracked leg state are retained.
The dashboard Schedule Manager labels every entry as **Calendar** or **Pasted**;
expanded calendar entries also show the originating calendar event summary.
When a calendar event matches a pasted pairing, the calendar version becomes
authoritative and future event revisions update that schedule in place.
When CrewHub supplies a layover hotel and telephone number, both dashboard
cards display it from the preceding duty's arrival until the next report time.

## Optional manual import

Manual paste remains available as a fallback for schedules that are not in the
calendar. In the Pilot Tracker card, choose **Import schedule** and paste either:

- Full pairing-detail text, including the heading that identifies Herb Time,
  Local Time, or Domicile Time.
- Compact local-time roster rows in the form
  `08/14/2026 3445 PHX 1640 SDF 2305`. Every departure and arrival is
  interpreted in that airport's local time. A trailing `Sent from my iPhone`
  line is accepted and ignored.

A compact single pairing is identified by its first date. A multi-pairing
monthly roster is identified by its dominant month, including a carry-in leg
from the preceding month. When a matching CrewHub calendar event exists, the
calendar-managed schedule becomes authoritative over the pasted copy.

Flattened Google Calendar descriptions are supported. CrewHub gate-return
records such as `882 PHX MST PHX MST` are retained as `GR` operational records
when their interval can be inferred from surrounding flights, but are excluded
from FlightRadar24 tracking; Pilot Tracker proceeds to the next airborne leg.
Delayed-report records such as `RPRT PHX 15:40 MST PHX 18:00 MST` are likewise
retained as non-trackable `RPRT` records, while the following numbered flight
remains the first flight submitted for live tracking.

Pairing-detail diversions such as flight 296 ELP→DAL marked `DV`, followed by
flight 296 DAL→SAT, are retained as two linked operational legs. Pilot Tracker
can match FlightRadar24's original ELP→SAT through-route while using DAL as
the actual arrival point for the diverted segment, then continues tracking the
same flight number from DAL to SAT.

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
