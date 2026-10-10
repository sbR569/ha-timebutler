"""The Timebutler integration."""

from __future__ import annotations

from homeassistant.const import CONF_API_TOKEN, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import TimebutlerClient
from .coordinator import (
    TimebutlerBackgroundCoordinator,
    TimebutlerConfigEntry,
    TimebutlerLiveCoordinator,
    TimebutlerRuntime,
)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: TimebutlerConfigEntry) -> bool:
    """Set up Timebutler from a config entry."""
    client = TimebutlerClient(async_get_clientsession(hass), entry.data[CONF_API_TOKEN])
    background = TimebutlerBackgroundCoordinator(hass, entry, client)
    live = TimebutlerLiveCoordinator(hass, entry, client, background)
    await background.async_config_entry_first_refresh()
    await live.async_config_entry_first_refresh()

    entry.runtime_data = TimebutlerRuntime(live=live, background=background)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TimebutlerConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
