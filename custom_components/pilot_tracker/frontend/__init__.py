"""Register the bundled Pilot Tracker dashboard card."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_call_later

from ..const import FRONTEND_URL, FRONTEND_VERSION


class FrontendRegistration:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def async_register(self) -> None:
        try:
            await self.hass.http.async_register_static_paths([
                StaticPathConfig(FRONTEND_URL, Path(__file__).parent, False)
            ])
        except RuntimeError:
            pass
        await self._async_wait_for_resources()

    async def _async_wait_for_resources(self) -> None:
        async def check(_now=None):
            lovelace = self.hass.data.get("lovelace")
            resources = getattr(lovelace, "resources", None)
            if not resources or not getattr(resources, "loaded", False):
                async_call_later(self.hass, 5, check)
                return
            url = f"{FRONTEND_URL}/pilot-tracker-card.js"
            existing = [item for item in resources.async_items() if item["url"].split("?")[0] == url]
            data = {"res_type": "module", "url": f"{url}?v={FRONTEND_VERSION}"}
            if existing:
                if existing[0]["url"] != data["url"]:
                    await resources.async_update_item(existing[0]["id"], data)
            else:
                await resources.async_create_item(data)
        await check()
