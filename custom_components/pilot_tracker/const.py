"""Constants for Pilot Tracker."""

from typing import Final

DOMAIN: Final = "pilot_tracker"
CONF_CALENDAR_ENTITY: Final = "calendar_entity"
PLATFORMS: Final = ["device_tracker", "sensor"]
STORAGE_KEY: Final = f"{DOMAIN}.trips"
STORAGE_VERSION: Final = 1
TIME_BASIS: Final = "America/Chicago"
FRONTEND_URL: Final = "/pilot_tracker_frontend"
FRONTEND_VERSION: Final = "2.4.0"
