"""IATA airport time-zone lookup."""

from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=1)
def load_airports() -> dict:
    """Load and cache airport data; call from an executor during setup."""
    import airportsdata

    return airportsdata.load("IATA")


def airport_timezone(iata_code: str) -> str:
    """Return an IANA time-zone name for an IATA airport code."""
    code = iata_code.strip().upper()
    airport = load_airports().get(code)
    timezone = airport.get("tz") if airport else None
    if not timezone:
        raise ValueError(f"No time zone found for airport {code}")
    return str(timezone)


def airport_coordinates(iata_code: str) -> tuple[float, float] | None:
    """Return latitude and longitude for an IATA airport when known."""
    airport = load_airports().get(iata_code.strip().upper())
    if not airport or airport.get("lat") is None or airport.get("lon") is None:
        return None
    return float(airport["lat"]), float(airport["lon"])


def airport_icao(iata_code: str) -> str:
    """Return the airport ICAO code, falling back to its IATA code."""
    code = iata_code.strip().upper()
    airport = load_airports().get(code)
    return str(airport.get("icao") or code) if airport else code
