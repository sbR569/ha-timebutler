"""Base entity for the Timebutler integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN
from .coordinator import TimebutlerConfigEntry, TimebutlerRuntime


class TimebutlerEntity(Entity):
    """Entity fed by one or more coordinators; available while the first one is."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _platform_domain: str

    def __init__(
        self,
        entry: TimebutlerConfigEntry,
        runtime: TimebutlerRuntime,
        key: str,
        sources: tuple[str, ...],
    ) -> None:
        self._runtime = runtime
        self._coordinators: list[DataUpdateCoordinator] = [
            getattr(runtime, source) for source in sources
        ]
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.unique_id}_{key}"
        # Fixed IDs, independent of the HA language, for dashboards and automations.
        self.entity_id = f"{self._platform_domain}.{DOMAIN}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(entry.unique_id))},
            name="Timebutler",
            manufacturer="Timebutler",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://app.timebutler.com",
        )

    @property
    def available(self) -> bool:
        """Available while the primary coordinator's last update succeeded."""
        return self._coordinators[0].last_update_success

    async def async_added_to_hass(self) -> None:
        """Write the state whenever one of the coordinators updates."""
        await super().async_added_to_hass()
        for coordinator in self._coordinators:
            self.async_on_remove(coordinator.async_add_listener(self.async_write_ha_state))
