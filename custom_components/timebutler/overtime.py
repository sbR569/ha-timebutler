"""Overtime balance ledger: start value, recorded daily targets, frozen days."""

from __future__ import annotations

from datetime import date, timedelta
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .model import day_absences, effective_target, overtime_days, weekday_norm

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
SAVE_DELAY = 10


def storage_key(entry_id: str) -> str:
    """Store key of one config entry's ledger."""
    return f"{DOMAIN}.{entry_id}.overtime"


class OvertimeLedger:
    """What the overtime balance needs across restarts.

    The start value (balance at the end of `as_of`) comes from the entry
    options; the store keeps the daily targets the live coordinator saw and
    the sum of days that are no longer fetched (frozen).
    """

    def __init__(
        self, hass: HomeAssistant, entry_id: str, balance_minutes: int | None, as_of: date | None
    ) -> None:
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, storage_key(entry_id))
        self.balance_minutes = balance_minutes
        self.as_of = as_of
        self.targets: dict[str, int] = {}
        self.frozen_through: date | None = None
        self.frozen_minutes = 0

    @property
    def configured(self) -> bool:
        """Whether a start value is set."""
        return self.balance_minutes is not None and self.as_of is not None

    async def async_load(self) -> None:
        """Read the store; forget frozen days when the start value changed."""
        try:
            data = await self._store.async_load() or {}
            targets = {str(key): int(value) for key, value in data.get("targets", {}).items()}
            frozen = data.get("frozen_through")
            frozen_through = date.fromisoformat(frozen) if frozen else None
            frozen_minutes = int(data.get("frozen_minutes", 0))
            stored_start = (data.get("balance_minutes"), data.get("as_of"))
        except (AttributeError, HomeAssistantError, TypeError, ValueError) as err:
            _LOGGER.warning("Ignoring the unreadable overtime store: %s", err)
            targets, frozen_through, frozen_minutes, stored_start = {}, None, 0, (None, None)
        self.targets = targets
        current_start = (self.balance_minutes, self.as_of.isoformat() if self.as_of else None)
        if stored_start == current_start:
            self.frozen_through, self.frozen_minutes = frozen_through, frozen_minutes
        else:
            self.frozen_through, self.frozen_minutes = None, 0
            self._schedule_save()

    def record_target(self, day: date, minutes: int) -> None:
        """Remember Timebutler's target for day (the latest value wins)."""
        key = day.isoformat()
        if self.targets.get(key) != minutes:
            self.targets[key] = minutes
            self._schedule_save()

    def counted_from(self) -> date | None:
        """First day that is neither covered by the start value nor frozen."""
        if self.as_of is None or self.balance_minutes is None:
            return None
        done = max(self.as_of, self.frozen_through) if self.frozen_through else self.as_of
        return done + timedelta(days=1)

    def freeze(self, today: date, entries: list[dict[str, Any]], requests: list[dict[str, Any]]) -> None:
        """Sum up the days before the previous month; they are no longer fetched."""
        start = self.counted_from()
        if start is None:
            return
        last = today.replace(day=1) - timedelta(days=1)  # end of the previous month
        last = last.replace(day=1) - timedelta(days=1)  # end of the month before that
        if start > last:
            return
        total, _ = overtime_days(start, last, entries, requests, self.targets)
        self.frozen_minutes += total
        self.frozen_through = last
        self._prune(requests)
        self._schedule_save()

    def _prune(self, requests: list[dict[str, Any]]) -> None:
        """Drop frozen targets except the latest per weekday without absence (the norms)."""
        assert self.frozen_through is not None
        cutoff = self.frozen_through.isoformat()
        norms: dict[int, str] = {}
        for key in sorted(self.targets):
            day = date.fromisoformat(key)
            if key <= cutoff and not day_absences(requests, day).any:
                norms[day.weekday()] = key
        keep = set(norms.values())
        self.targets = {key: value for key, value in self.targets.items() if key > cutoff or key in keep}

    def balance(
        self, today: date, entries: list[dict[str, Any]], requests: list[dict[str, Any]]
    ) -> tuple[int, list[str]] | None:
        """Balance at the end of yesterday, and the days with an estimated target."""
        start = self.counted_from()
        if start is None or self.balance_minutes is None:
            return None
        total, estimated = overtime_days(start, today - timedelta(days=1), entries, requests, self.targets)
        return self.balance_minutes + self.frozen_minutes + total, estimated

    def today_difference(self, today: date, worked: int, target: int, requests: list[dict[str, Any]]) -> int:
        """Today's worked minus target with the absence rules, from live values."""
        absences = day_absences(requests, today)
        norm = weekday_norm(today, self.targets, requests)
        return worked - effective_target(target, norm, absences) + absences.gain_minutes

    async def async_save(self) -> None:
        """Write the store now."""
        await self._store.async_save(self._data())

    @staticmethod
    async def async_remove(hass: HomeAssistant, entry_id: str) -> None:
        """Delete a removed entry's store file."""
        await Store(hass, STORAGE_VERSION, storage_key(entry_id)).async_remove()

    def _schedule_save(self) -> None:
        self._store.async_delay_save(self._data, SAVE_DELAY)

    def _data(self) -> dict[str, Any]:
        return {
            "targets": self.targets,
            "frozen_through": self.frozen_through.isoformat() if self.frozen_through else None,
            "frozen_minutes": self.frozen_minutes,
            "balance_minutes": self.balance_minutes,
            "as_of": self.as_of.isoformat() if self.as_of else None,
        }
