"""Time clock buttons for the Timebutler integration."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .actions import async_clock_action, async_stop
from .coordinator import TimebutlerConfigEntry, TimebutlerRuntime
from .entity import TimebutlerEntity
from .model import ClockState

PressFn = Callable[[HomeAssistant, TimebutlerConfigEntry], Awaitable[Any]]

STOPPABLE = ("running", "paused", "waiting")


def _clock(action: str) -> PressFn:
    def press(hass: HomeAssistant, entry: TimebutlerConfigEntry) -> Awaitable[None]:
        return async_clock_action(hass, entry, action)

    return press


@dataclass(frozen=True, kw_only=True)
class TimebutlerButtonDescription(ButtonEntityDescription):
    """Button with the clock states it is usable in and its action."""

    available_fn: Callable[[ClockState], bool]
    press_fn: PressFn


BUTTONS: tuple[TimebutlerButtonDescription, ...] = (
    TimebutlerButtonDescription(
        key="start",
        available_fn=lambda clock: clock.status == "idle",
        press_fn=_clock("start"),
    ),
    TimebutlerButtonDescription(
        key="pause",
        available_fn=lambda clock: clock.status == "running",
        press_fn=_clock("pause"),
    ),
    TimebutlerButtonDescription(
        key="fortsetzen",
        available_fn=lambda clock: clock.status == "paused",
        press_fn=_clock("resume"),
    ),
    TimebutlerButtonDescription(
        key="stopp",
        available_fn=lambda clock: clock.status in STOPPABLE,
        press_fn=lambda hass, entry: async_stop(hass, entry),
    ),
    TimebutlerButtonDescription(
        key="stopp_pausenkorrektur",
        available_fn=lambda clock: clock.clocked_in,
        press_fn=lambda hass, entry: async_stop(hass, entry, break_regulation="apply"),
    ),
    TimebutlerButtonDescription(
        key="verwerfen",
        entity_registry_enabled_default=False,
        available_fn=lambda clock: clock.status in STOPPABLE,
        press_fn=_clock("cancel"),
    ),
    TimebutlerButtonDescription(
        key="dienstreise_start",
        entity_registry_enabled_default=False,
        available_fn=lambda clock: clock.clocked_in and not clock.business_trip,
        press_fn=_clock("business_trip_start"),
    ),
    TimebutlerButtonDescription(
        key="dienstreise_ende",
        entity_registry_enabled_default=False,
        available_fn=lambda clock: clock.clocked_in and clock.business_trip,
        press_fn=_clock("business_trip_end"),
    ),
)


class TimebutlerButton(TimebutlerEntity, ButtonEntity):
    """A time clock action; unavailable while the clock state does not allow it."""

    _platform_domain = "button"
    entity_description: TimebutlerButtonDescription

    def __init__(
        self,
        entry: TimebutlerConfigEntry,
        runtime: TimebutlerRuntime,
        description: TimebutlerButtonDescription,
    ) -> None:
        super().__init__(entry, runtime, description.key, ("live",))
        self.entity_description = description
        self._entry = entry

    @property
    def available(self) -> bool:
        data = self._runtime.live.data
        return (
            super().available
            and data is not None
            and data.clock is not None
            and self.entity_description.available_fn(data.clock)
        )

    async def async_press(self) -> None:
        await self.entity_description.press_fn(self.hass, self._entry)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TimebutlerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the time clock buttons."""
    async_add_entities(TimebutlerButton(entry, entry.runtime_data, description) for description in BUTTONS)
