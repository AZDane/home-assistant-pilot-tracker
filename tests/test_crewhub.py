from datetime import date

import pytest

from custom_components.pilot_tracker.providers.base import ScheduleParseError
from custom_components.pilot_tracker.providers.crewhub import CrewHubCalendarProvider


SAMPLE = """LOCAL

Thu Sep 10
Report 15:05 MST
2327 PHX 16:05 MST   LAX 17:30 PDT
2327 LAX 18:10 PDT   OAK 19:30 PDT
2327 OAK 20:10 PDT   PDX 21:50 PDT
Duty 7:15 Block 4:25 Credit 5.37 D

Fri Sep 11
Report 11:05 PDT
1351 PDX 11:35 PDT   MDW 17:35 CDT
4587 MDW 18:45 CDT   JAX 22:05 EDT

Sat Sep 12
Report 16:40 EDT
4107 JAX 17:10 EDT   DEN 18:45 MDT
1461 DEN 19:30 MDT   BOI 21:35 MDT

Sun Sep 13
Report 13:55 MDT
3197 BOI 14:25 MDT   OAK 15:10 PDT
872 OAK 15:55 PDT   SAN 17:25 PDT
391 SAN 18:10 PDT   SJC 19:30 PDT
3618 SJC 20:15 PDT   PHX 22:10 MST

Totals: Duty 32:55 Block 22:55 Credit 26.87 &#x20;
Synced from CrewHub BYO Version 8.5.1
August 30 09:21 MDT
"""

ZONES = {
    "PHX": "America/Phoenix", "LAX": "America/Los_Angeles",
    "OAK": "America/Los_Angeles", "PDX": "America/Los_Angeles",
    "MDW": "America/Chicago", "JAX": "America/New_York",
    "DEN": "America/Denver", "BOI": "America/Boise",
    "SAN": "America/Los_Angeles", "SJC": "America/Los_Angeles",
}


def test_parses_crewhub_calendar_pairing_in_airport_local_time():
    trip = CrewHubCalendarProvider(ZONES.__getitem__).parse(
        SAMPLE, anchor_date=date(2026, 9, 10), identifier="CAL-2026-09-10"
    )

    assert trip.trip_id == "CAL-2026-09-10"
    assert trip.source == "crewhub_calendar"
    assert trip.time_basis == "airport_local"
    assert len(trip.legs) == 11
    assert [leg.duty_period for leg in trip.legs] == [1, 1, 1, 2, 2, 3, 3, 4, 4, 4, 4]
    assert trip.legs[0].scheduled_departure.isoformat() == "2026-09-10T16:05:00-07:00"
    assert trip.legs[0].scheduled_arrival.isoformat() == "2026-09-10T17:30:00-07:00"
    assert trip.legs[3].scheduled_arrival.isoformat() == "2026-09-11T17:35:00-05:00"
    assert trip.legs[-1].destination == "PHX"


def test_year_rolls_forward_across_new_year():
    text = """LOCAL
Thu Dec 31
1 PHX 23:00 MST LAX 23:30 PST
Fri Jan 1
2 LAX 01:00 PST PHX 03:30 MST
"""
    trip = CrewHubCalendarProvider(ZONES.__getitem__).parse(
        text, anchor_date=date(2026, 12, 31)
    )

    assert trip.legs[0].date == "2026-12-31"
    assert trip.legs[1].date == "2027-01-01"


def test_rejects_zone_abbreviation_that_disagrees_with_airport_date():
    text = """LOCAL
Thu Sep 10
2327 PHX 16:05 MDT LAX 17:30 PDT
"""
    with pytest.raises(ScheduleParseError, match="does not match PHX"):
        CrewHubCalendarProvider(ZONES.__getitem__).parse(
            text, anchor_date=date(2026, 9, 10)
        )


def test_html_breaks_and_entities_are_normalized():
    text = "LOCAL<br>Thu Sep 10<br>2327 PHX 16:05 MST LAX 17:30 PDT<br>Credit 5.37 &#x20;"
    trip = CrewHubCalendarProvider(ZONES.__getitem__).parse(
        text, anchor_date=date(2026, 9, 10)
    )
    assert len(trip.legs) == 1


def test_flattened_calendar_text_and_gate_return_are_supported():
    text = """12:55 MST GEG DEN PHX 23:50 MST July 24 – 26, 2026
    Take meeting notes Start a new document to capture notes Trip: PP2O
    LOCAL Fri Jul 24 Report 12:55 MST
    670 PHX 13:55 MST ONT 15:05 PDT
    670 ONT 15:40 PDT SMF 17:05 PDT
    670 SMF 17:40 PDT DEN 21:00 MDT
    1538 DEN 22:05 MDT GEG 23:30 PDT
    DUTY GEG 00:15 PDT GEG 00:15 PDT Duty 11:20 Block 6:32
    Sat Jul 25 Report 11:40 PDT
    882 GEG 12:10 PDT PHX 14:45 MST
    882 PHX MST PHX MST
    882 PHX 15:25 MST ABQ 17:35 MDT
    882 ABQ 18:10 MDT DEN 19:40 MDT Duty 9:59 Block 5:29 Credit 8.90 G
    Sun Jul 26 Report 18:15 MDT
    136 DEN 18:45 MDT SMF 20:15 PDT
    3817 SMF 20:55 PDT PHX 22:55 MST
    Synced from CrewHub BYO Version 8.4.0 August 13 11:15 MST"""
    zones = {
        **ZONES,
        "ONT": "America/Los_Angeles",
        "SMF": "America/Los_Angeles",
        "GEG": "America/Los_Angeles",
        "ABQ": "America/Denver",
    }

    trip = CrewHubCalendarProvider(zones.__getitem__).parse(
        text, anchor_date=date(2026, 7, 24)
    )

    assert trip.trip_id == "PP2O"
    assert len(trip.legs) == 10
    gate_return = next(leg for leg in trip.legs if leg.qualifier == "GR")
    assert gate_return.origin == gate_return.destination == "PHX"
    assert gate_return.status.value == "skipped"
    assert gate_return.scheduled_departure.isoformat() == "2026-07-25T14:45:00-07:00"
    assert gate_return.scheduled_arrival.isoformat() == "2026-07-25T15:25:00-07:00"
    assert trip.metadata["gate_return_count"] == 1
    following = trip.legs[gate_return.sequence]
    assert (following.origin, following.destination) == ("PHX", "ABQ")
