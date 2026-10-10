"""Coordinators for the Timebutler integration."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    TimebutlerAuthError,
    TimebutlerClient,
    TimebutlerConnectionError,
    TimebutlerTimeTrackingDisabled,
)
from .const import (
    BACKGROUND_INTERVAL,
    DOMAIN,
    LIVE_INTERVAL,
    WORK_SUMMARY_INCLUDES_RUNNING,
)
from .model import ClockState, TodaySummary, months_needed, parse_clock, today_summary

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class LiveData:
    """Clock and today's minutes; both None when time tracking is off."""

    clock: ClockState | None
    today: TodaySummary | None


@dataclass(frozen=True)
class BackgroundData:
    """Slowly changing data: vacation, absences, time entries."""

    time_off: dict[str, Any] | None
    absences: list[dict[str, Any]]
    absence_types: dict[str, str]
    entries: list[dict[str, Any]]


class TimebutlerBackgroundCoordinator(DataUpdateCoordinator[BackgroundData]):
    """Polls vacation, absences and time entries every 15 minutes."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: TimebutlerClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} background",
            update_interval=BACKGROUND_INTERVAL,
        )
        self.client = client

    async def _async_update_data(self) -> BackgroundData:
        today = dt_util.now().date()
        try:
            try:
                time_off = await self.client.get_time_off_summary()
            except TimebutlerTimeTrackingDisabled:
                time_off = None
            absences = await self.client.get_absence_requests()
            types = {
                str(item.get("id")): str(item.get("name"))
                for item in await self.client.get_absence_types()
            }
            entries: list[dict[str, Any]] = []
            for year, month in months_needed(today):
                entries.extend(await self.client.get_time_entries(year, month))
        except TimebutlerAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except TimebutlerConnectionError as err:
            raise UpdateFailed(str(err)) from err
        return BackgroundData(time_off=time_off, absences=absences, absence_types=types, entries=entries)


class TimebutlerLiveCoordinator(DataUpdateCoordinator[LiveData]):
    """Polls the clock and today's summary every minute."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: TimebutlerClient,
        background: TimebutlerBackgroundCoordinator,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} live",
            update_interval=LIVE_INTERVAL,
        )
        self.client = client
        self._background = background

    async def _async_update_data(self) -> LiveData:
        try:
            clock = parse_clock(await self.client.get_clock_status())
            summary = await self.client.get_work_summary()
        except TimebutlerTimeTrackingDisabled:
            return LiveData(clock=None, today=None)
        except TimebutlerAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except TimebutlerConnectionError as err:
            raise UpdateFailed(str(err)) from err

        previous = self.data.clock if self.data is not None else None
        if previous is not None and previous.clocked_in and not clock.clocked_in:
            # The finished session is now a time entry: refresh week/month soon.
            self.config_entry.async_create_background_task(
                self.hass,
                self._background.async_request_refresh(),
                name=f"{DOMAIN} refresh after clock-out",
            )
        return LiveData(
            clock=clock,
            today=today_summary(summary, clock, WORK_SUMMARY_INCLUDES_RUNNING, dt_util.now()),
        )


@dataclass
class TimebutlerRuntime:
    """Coordinators of one config entry."""

    live: TimebutlerLiveCoordinator
    background: TimebutlerBackgroundCoordinator


type TimebutlerConfigEntry = ConfigEntry[TimebutlerRuntime]
