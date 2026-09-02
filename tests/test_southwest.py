from datetime import timedelta

import pytest

from custom_components.pilot_tracker.providers.base import ScheduleParseError
from custom_components.pilot_tracker.providers.southwest import SouthwestPairingProvider


SAMPLE = """PAIRING: PAGR
HERB TIME/ESTIMATED
07Aug 3206 PHX 2100 IND 0017
08Aug 403  IND 1310 DEN 1543
08Aug 4744 DEN 1754 SJC 2019
09Aug 3566 SJC 1607 AUS 1929
09Aug 2240 AUS 2019 TPA 2236
10Aug 2388 TPA 1731 SAN 2159
11Aug 1424 SAN 0111 PHX 0225
"""

REAL_LAYOUT = """Trip PAGR dated 07Aug26
*********************** HERB TIME/ESTIMATED ***********************
 Date Flight Depart Arrive Eq Blk
 07Aug 3206 PHX 2100 IND 0017 7P8 317
 08Aug 403 IND 1310 DEN 1543 7A8 233
 08Aug 4744 DEN 1754 SJC 2019 7U8 225
 09Aug 3566 SJC 1607 AUS 1929 7R8 322
 09Aug 2240 AUS 2019 TPA 2236 7R8 217
 10Aug 2388 TPA 1731 SAN 2159 7U8 428
 11Aug 1424 SAN 0111 PHX 0225 7X8 114
C R E W O N T R I P
1 CA 01 L 12345 6789 TEST CREW
"""

PRINT_VIEW_TABLE = """Trip PA7K on 08/14/2026
Herb Time <- Click to toggle. -> Estimated Totals
Date Flight Depart Arrive Eq Block
Rpt 1740
14 Aug 3445 PHX 1840 SDF 2205 7X8 325
Rls 2235
15 Aug 4898 SDF 1550 LAS 1955 7Y8 405
15 Aug 1263 LAS 2045 SNA 2150 7Y8 105
Rls 2220
16 Aug 2761 SNA 1415 SMF 1545 7X8 130
16 Aug 3751 SMF 1625 SNA 1755 7X8 130
16 Aug 3770 SNA 2005 OAK 2125 7X8 120
16 Aug 4647 OAK 2210 PHX 0005 7X8 155
Rls 0035
"""


def test_parses_representative_pairing():
    trip = SouthwestPairingProvider().parse(SAMPLE, year=2026)
    assert trip.trip_id == "PAGR"
    assert len(trip.legs) == 7
    assert [(leg.origin, leg.destination) for leg in trip.legs] == [
        ("PHX", "IND"), ("IND", "DEN"), ("DEN", "SJC"), ("SJC", "AUS"),
        ("AUS", "TPA"), ("TPA", "SAN"), ("SAN", "PHX")]


def test_midnight_rollover_and_central_dst():
    leg = SouthwestPairingProvider().parse(SAMPLE, year=2026).legs[0]
    assert leg.scheduled_departure.isoformat() == "2026-08-07T21:00:00-05:00"
    assert leg.scheduled_arrival.isoformat() == "2026-08-08T00:17:00-05:00"
    assert leg.scheduled_arrival - leg.scheduled_departure == timedelta(hours=3, minutes=17)


def test_requires_explicit_supported_time_mode():
    with pytest.raises(ScheduleParseError, match="Herb Time, Local Time, or Domicile Time"):
        SouthwestPairingProvider().parse(SAMPLE.replace("HERB TIME/ESTIMATED", "UTC TIME"), year=2026)


def test_parses_real_pdf_layout_header_and_two_digit_year():
    trip = SouthwestPairingProvider().parse(REAL_LAYOUT)
    assert trip.trip_id == "PAGR"
    assert trip.legs[0].date == "2026-08-07"
    assert "PRIVATE" not in repr(trip.to_dict())


def test_duty_periods_and_deadhead_marker():
    text = """Trip NP25 dated 10Aug26
HERB TIME/ESTIMATED
10Aug 2746 BNA 0649 LGA 0901
10Aug 468 LGA 0956 DEN 1408
10Aug DM 468 DEN 1547 PHX 1729
Rpt 0530 Rls 1759
11Aug 3207 PHX 0745 LAS 0855
11Aug 116 LAS 0945 OMA 1225
Rpt 0715 Rls 1255
12Aug 3070 OMA 0820 STL 0935
"""
    trip = SouthwestPairingProvider().parse(text)
    assert [leg.duty_period for leg in trip.legs] == [1, 1, 1, 2, 2, 3]
    assert trip.legs[2].qualifier == "DM"
    assert (trip.legs[2].origin, trip.legs[2].destination) == ("DEN", "PHX")


def test_parses_diversion_and_same_number_continuation():
    text = """Trip DIV1 dated 19Jun26
HERB TIME/ESTIMATED
19 Jun 2254 PHX 2016 SAT 2213 7A8 157
19 Jun 1822 SAT 2258 ELP 0033 7A8 135
20 Jun 296 ELP 0102 DAL 0358 7A8 256 130 DV
20 Jun 296 DAL 0528 SAT 0626 7A8 058
Rpt 1725 Rls 0656
"""
    trip = SouthwestPairingProvider().parse(text)

    assert [(leg.flight_number, leg.origin, leg.destination, leg.qualifier) for leg in trip.legs] == [
        ("2254", "PHX", "SAT", None),
        ("1822", "SAT", "ELP", None),
        ("296", "ELP", "DAL", "DV"),
        ("296", "DAL", "SAT", "DV-CONT"),
    ]


def test_parses_print_view_table_with_spaced_dates_and_on_header():
    trip = SouthwestPairingProvider().parse(PRINT_VIEW_TABLE)

    assert trip.trip_id == "PA7K"
    assert len(trip.legs) == 7
    assert [leg.duty_period for leg in trip.legs] == [1, 2, 2, 3, 3, 3, 3]
    assert [(leg.origin, leg.destination) for leg in trip.legs] == [
        ("PHX", "SDF"), ("SDF", "LAS"), ("LAS", "SNA"),
        ("SNA", "SMF"), ("SMF", "SNA"), ("SNA", "OAK"), ("OAK", "PHX"),
    ]
    assert trip.legs[-1].scheduled_arrival.isoformat() == "2026-08-17T00:05:00-05:00"


AIRPORT_TIMEZONES = {
    "BNA": "America/Chicago",
    "DEN": "America/Denver",
    "PHX": "America/Phoenix",
    "SDF": "America/Kentucky/Louisville",
}


def airport_timezone(code):
    return AIRPORT_TIMEZONES[code]


def test_local_time_uses_origin_and_destination_airport_zones():
    text = """Trip LOCAL1 on 08/14/2026
LOCAL TIME
14 Aug 3445 PHX 1840 SDF 2205
"""
    trip = SouthwestPairingProvider(airport_timezone).parse(text)
    leg = trip.legs[0]

    assert trip.time_basis == "airport_local"
    assert trip.metadata["schedule_time_mode"] == "local"
    assert leg.scheduled_departure.isoformat() == "2026-08-14T18:40:00-07:00"
    assert leg.scheduled_arrival.isoformat() == "2026-08-14T22:05:00-04:00"


def test_domicile_time_uses_first_origin_zone_for_every_leg():
    text = """Trip DOM1 on 08/14/2026
DOMICILE TIME
14 Aug 100 PHX 1000 DEN 1200
15 Aug 101 DEN 1300 BNA 1600
"""
    trip = SouthwestPairingProvider(airport_timezone).parse(text)

    assert trip.time_basis == "America/Phoenix"
    assert trip.metadata == {"schedule_time_mode": "domicile", "domicile_airport": "PHX"}
    assert all(leg.scheduled_departure.utcoffset() == timedelta(hours=-7) for leg in trip.legs)
    assert all(leg.scheduled_arrival.utcoffset() == timedelta(hours=-7) for leg in trip.legs)


LOCAL_ROSTER_TIMEZONES = {
    "PHX": "America/Phoenix",
    "SDF": "America/Kentucky/Louisville",
    "LAS": "America/Los_Angeles",
    "SNA": "America/Los_Angeles",
    "SMF": "America/Los_Angeles",
    "OAK": "America/Los_Angeles",
}


def local_roster_timezone(code):
    return LOCAL_ROSTER_TIMEZONES[code]


def test_parses_local_airport_time_roster_with_optional_iphone_signature():
    text = """08/14/2026 3445 PHX 1640 SDF 2305
08/15/2026 4898 SDF 1650 LAS 1755
08/15/2026 1263 LAS 1845 SNA 1950
08/16/2026 2761 SNA 1215 SMF 1345
08/16/2026 3751 SMF 1425 SNA 1555
08/16/2026 3770 SNA 1805 OAK 1925
08/16/2026 4647 OAK 2010 PHX 2205
Sent from my iPhone
"""
    trip = SouthwestPairingProvider(local_roster_timezone).parse(text)

    assert trip.trip_id == "2026-08-14"
    assert trip.source == "southwest_local_roster"
    assert trip.time_basis == "airport_local"
    assert trip.metadata == {
        "schedule_time_mode": "local",
        "format": "local_roster",
        "identifier_basis": "start_date",
    }
    assert [leg.duty_period for leg in trip.legs] == [1, 2, 2, 3, 3, 3, 3]
    assert trip.legs[0].scheduled_departure.isoformat() == "2026-08-14T16:40:00-07:00"
    assert trip.legs[0].scheduled_arrival.isoformat() == "2026-08-14T23:05:00-04:00"


def test_month_roster_uses_dominant_month_identifier_and_local_rollover():
    text = """07/31/2026 4600 PHX 1520 SDF 2040
08/01/2026 2278 SDF 1345 PHX 1505
08/14/2026 3445 PHX 1640 SDF 2305
08/15/2026 4898 SDF 1650 LAS 1755
08/21/2026 3445 PHX 1640 SDF 2305
08/22/2026 4898 SDF 1650 LAS 1755
"""
    trip = SouthwestPairingProvider(local_roster_timezone).parse(text)

    assert trip.trip_id == "AUG-2026"
    assert trip.metadata["identifier_basis"] == "month"
    assert len(trip.legs) == 6


def test_local_roster_adds_day_when_arrival_is_after_midnight_locally():
    text = "08/14/2026 3445 SDF 2300 PHX 0010"
    trip = SouthwestPairingProvider(local_roster_timezone).parse(text)

    assert trip.legs[0].scheduled_arrival.date().isoformat() == "2026-08-15"


def test_local_roster_recognition_rejects_unrecognized_extra_text():
    text = """08/14/2026 3445 PHX 1640 SDF 2305
These times are local
"""
    with pytest.raises(ScheduleParseError, match="Herb Time, Local Time, or Domicile Time"):
        SouthwestPairingProvider(local_roster_timezone).parse(text)
