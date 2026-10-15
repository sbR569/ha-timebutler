"""The Timebutler integration."""

from __future__ import annotations

from datetime import date

from homeassistant.const import CONF_API_TOKEN, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import TimebutlerClient
from .const import DOMAIN, OPT_AS_OF, OPT_BALANCE_MINUTES
from .coordinator import (
    TimebutlerBackgroundCoordinator,
    TimebutlerConfigEntry,
    TimebutlerLiveCoordinator,
    TimebutlerRuntime,
)
from .overtime import OvertimeLedger
from .services import async_setup_services

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SENSOR]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the services once, for all entries."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: TimebutlerConfigEntry) -> bool:
    """Set up Timebutler from a config entry."""
    client = TimebutlerClient(async_get_clientsession(hass), entry.data[CONF_API_TOKEN])
    ledger = OvertimeLedger(hass, entry.entry_id, entry.options.get(OPT_BALANCE_MINUTES), _as_of(entry))
    await ledger.async_load()
    background = TimebutlerBackgroundCoordinator(hass, entry, client, ledger)
    live = TimebutlerLiveCoordinator(hass, entry, client, background)
    await background.async_config_entry_first_refresh()
    await live.async_config_entry_first_refresh()

    entry.runtime_data = TimebutlerRuntime(live=live, background=background, ledger=ledger)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _as_of(entry: TimebutlerConfigEntry) -> date | None:
    value = entry.options.get(OPT_AS_OF)
    return date.fromisoformat(value) if value else None


async def _async_options_updated(hass: HomeAssistant, entry: TimebutlerConfigEntry) -> None:
    """A new overtime start value: rebuild the ledger."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: TimebutlerConfigEntry) -> bool:
    """Unload a config entry; write the ledger now instead of after the save delay."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.ledger.async_save()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: TimebutlerConfigEntry) -> None:
    """Delete the overtime store of a removed entry."""
    await OvertimeLedger.async_remove(hass, entry.entry_id)
