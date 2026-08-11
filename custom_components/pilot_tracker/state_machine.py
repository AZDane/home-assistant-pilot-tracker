"""Explicit state scaffold for later tracking phases."""

from enum import StrEnum


class PilotTrackerState(StrEnum):
    NO_SCHEDULE = "no_schedule"
    WAITING_FOR_DUTY = "waiting_for_duty"
    WAITING_FOR_FIRST_LEG = "waiting_for_first_leg"
    RESOLVING_FLIGHT = "resolving_flight"
    TRACKING_FLIGHT = "tracking_flight"
    ARRIVAL_PENDING = "arrival_pending"
    TURNAROUND = "turnaround"
    WAITING_FOR_NEXT_LEG = "waiting_for_next_leg"
    TRIP_COMPLETE = "trip_complete"
    ERROR = "error"
