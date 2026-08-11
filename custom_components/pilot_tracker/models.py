"""Persistent, Home Assistant-independent schedule models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class LegStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    ERROR = "error"
    SKIPPED = "skipped"


class TripStatus(StrEnum):
    ACTIVE = "active"
    COMPLETE = "complete"
    ARCHIVED = "archived"


@dataclass(slots=True)
class FlightLeg:
    sequence: int
    date: str
    flight_number: str
    airline: str
    origin: str
    destination: str
    scheduled_departure: datetime
    scheduled_arrival: datetime
    status: LegStatus = LegStatus.PENDING
    tracking_identifiers: dict[str, str] = field(default_factory=dict)
    duty_period: int = 1
    qualifier: str | None = None

    @property
    def identity(self) -> tuple[str, str, str, str]:
        return (self.date, self.flight_number, self.origin, self.destination)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["scheduled_departure"] = self.scheduled_departure.isoformat()
        data["scheduled_arrival"] = self.scheduled_arrival.isoformat()
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FlightLeg:
        values = dict(data)
        values["scheduled_departure"] = datetime.fromisoformat(values["scheduled_departure"])
        values["scheduled_arrival"] = datetime.fromisoformat(values["scheduled_arrival"])
        values["status"] = LegStatus(values.get("status", LegStatus.PENDING))
        return cls(**values)


@dataclass(slots=True)
class Trip:
    trip_id: str
    source: str
    time_basis: str
    legs: list[FlightLeg]
    status: TripStatus = TripStatus.ACTIVE
    revision_date: str | None = None
    current_leg_sequence: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def current_leg(self) -> FlightLeg | None:
        if self.current_leg_sequence is None:
            return None
        return next((leg for leg in self.legs if leg.sequence == self.current_leg_sequence), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trip_id": self.trip_id,
            "source": self.source,
            "time_basis": self.time_basis,
            "status": self.status.value,
            "revision_date": self.revision_date,
            "current_leg_sequence": self.current_leg_sequence,
            "metadata": self.metadata,
            "legs": [leg.to_dict() for leg in self.legs],
        }

    @property
    def key(self) -> str:
        first_date = self.legs[0].date if self.legs else "undated"
        return f"{self.trip_id}:{first_date}"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Trip:
        values = dict(data)
        values["legs"] = [FlightLeg.from_dict(item) for item in values["legs"]]
        values["status"] = TripStatus(values.get("status", TripStatus.ACTIVE))
        return cls(**values)
