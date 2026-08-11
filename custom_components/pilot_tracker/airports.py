"""IATA airport time-zone lookup."""

from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=1)
def _iata_airports() -> dict:
    import airportsdata

    return airportsdata.load("IATA")


def airport_timezone(iata_code: str) -> str:
    """Return an IANA time-zone name for an IATA airport code."""
    code = iata_code.strip().upper()
    airport = _iata_airports().get(code)
    timezone = airport.get("tz") if airport else None
    if not timezone:
        raise ValueError(f"No time zone found for airport {code}")
    return str(timezone)
