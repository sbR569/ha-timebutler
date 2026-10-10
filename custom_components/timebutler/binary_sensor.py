"""Binary sensor for the Timebutler integration."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import TimebutlerConfigEntry, TimebutlerRuntime
from .entity import TimebutlerEntity


class TimebutlerClockedInSensor(TimebutlerEntity, BinarySensorEntity):
    """On while the clock runs or is paused."""

    _platform_domain = "binary_sensor"

    def __init__(self, entry: TimebutlerConfigEntry, runtime: TimebutlerRuntime) -> None:
        super().__init__(entry, runtime, "eingestempelt", ("live",))

    @property
    def is_on(self) -> bool | None:
        data = self._runtime.live.data
        if data is None or data.clock is None or data.clock.status is None:
            return None
        return data.clock.clocked_in


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TimebutlerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the clocked-in sensor."""
    async_add_entities([TimebutlerClockedInSensor(entry, entry.runtime_data)])
