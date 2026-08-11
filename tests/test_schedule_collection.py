from datetime import timedelta

import pytest

from custom_components.pilot_tracker.providers.southwest import SouthwestPairingProvider
from custom_components.pilot_tracker.schedule import ScheduleLimitError, validate_collection_horizon
from tests.test_southwest import SAMPLE


def test_two_month_collection_is_allowed():
    first = SouthwestPairingProvider().parse(SAMPLE, year=2026)
    second = SouthwestPairingProvider().parse(SAMPLE.replace("PAGR", "NEXT"), year=2026)
    for leg in second.legs:
        leg.scheduled_departure += timedelta(days=60)
        leg.scheduled_arrival += timedelta(days=60)
        leg.date = leg.scheduled_departure.date().isoformat()
    validate_collection_horizon([first], second)


def test_collection_over_62_days_is_rejected():
    first = SouthwestPairingProvider().parse(SAMPLE, year=2026)
    second = SouthwestPairingProvider().parse(SAMPLE.replace("PAGR", "NEXT"), year=2026)
    for leg in second.legs:
        leg.scheduled_departure += timedelta(days=70)
        leg.scheduled_arrival += timedelta(days=70)
        leg.date = leg.scheduled_departure.date().isoformat()
    with pytest.raises(ScheduleLimitError):
        validate_collection_horizon([first], second)
