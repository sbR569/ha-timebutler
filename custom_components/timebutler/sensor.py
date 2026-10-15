"""Sensors for the Timebutler integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import UPCOMING_LIMIT
from .coordinator import TimebutlerConfigEntry, TimebutlerRuntime
from .entity import TimebutlerEntity
from .model import (
    CLOCK_STATES,
    ClockState,
    TodaySummary,
    month_minutes,
    upcoming_absences,
    week_minutes,
)

ValueFn = Callable[[TimebutlerRuntime], Any]


def _clock(rt: TimebutlerRuntime) -> ClockState | None:
    return rt.live.data.clock if rt.live.data else None


def _today(rt: TimebutlerRuntime) -> TodaySummary | None:
    return rt.live.data.today if rt.live.data else None


def _today_field(field: str) -> ValueFn:
    def value(rt: TimebutlerRuntime) -> int | None:
        today = _today(rt)
        return getattr(today, field) if today else None

    return value


def _days(fn: Callable[[list[dict[str, Any]], date, int | None], dict[str, int]]) -> ValueFn:
    def days(rt: TimebutlerRuntime) -> dict[str, int] | None:
        background = rt.background.data
        if background is None:
            return None
        today = _today(rt)
        return fn(background.entries, dt_util.now().date(), today.worked if today else None)

    return days


_week = _days(week_minutes)
_month = _days(month_minutes)


def _total(days_fn: ValueFn) -> ValueFn:
    def value(rt: TimebutlerRuntime) -> int | None:
        days = days_fn(rt)
        return sum(days.values()) if days is not None else None

    return value


def _time_off(key: str) -> ValueFn:
    def value(rt: TimebutlerRuntime) -> float | None:
        background = rt.background.data
        return background.time_off.get(key) if background and background.time_off else None

    return value


def _upcoming(rt: TimebutlerRuntime) -> list[dict[str, Any]]:
    background = rt.background.data
    if background is None:
        return []
    return upcoming_absences(
        background.absences, background.absence_types, dt_util.now().date(), UPCOMING_LIMIT
    )


def _clock_state(rt: TimebutlerRuntime) -> str | None:
    clock = _clock(rt)
    return clock.status if clock else None


def _clock_attrs(rt: TimebutlerRuntime) -> dict[str, Any] | None:
    clock = _clock(rt)
    if clock is None:
        return None
    started = clock.started.isoformat() if clock.clocked_in and clock.started else None
    return {"seit": started, "dienstreise": clock.business_trip}


def _week_attrs(rt: TimebutlerRuntime) -> dict[str, Any]:
    return {"tage": _week(rt)}


def _month_attrs(rt: TimebutlerRuntime) -> dict[str, Any]:
    days = _month(rt) or {}
    return {"tage": days, "arbeitstage": sum(1 for minutes in days.values() if minutes > 0)}


def _vacation_attrs(rt: TimebutlerRuntime) -> dict[str, Any]:
    return {
        "gesamt": _time_off("vacationDaysTotal")(rt),
        "genehmigt": _time_off("vacationDaysApproved")(rt),
        "beantragt": _time_off("vacationDaysPending")(rt),
    }


def _next_absence(rt: TimebutlerRuntime) -> date | None:
    upcoming = _upcoming(rt)
    return date.fromisoformat(upcoming[0]["von"]) if upcoming else None


def _next_absence_attrs(rt: TimebutlerRuntime) -> dict[str, Any]:
    upcoming = _upcoming(rt)
    first = {key: value for key, value in upcoming[0].items() if key != "von"} if upcoming else {}
    return {**first, "liste": upcoming}


def _overtime(rt: TimebutlerRuntime) -> tuple[int, list[str]] | None:
    background = rt.background.data
    if background is None:
        return None
    return rt.ledger.balance(dt_util.now().date(), background.entries, background.absences)


def _overtime_value(rt: TimebutlerRuntime) -> int | None:
    result = _overtime(rt)
    return result[0] if result else None


def _overtime_attrs(rt: TimebutlerRuntime) -> dict[str, Any] | None:
    result = _overtime(rt)
    if result is None or rt.background.data is None or rt.ledger.as_of is None:
        return None
    balance, estimated = result
    today = _today(rt)
    with_today = None
    if today is not None:
        with_today = balance + rt.ledger.today_difference(
            dt_util.now().date(), today.worked, today.commitment, rt.background.data.absences
        )
    return {"mit_heute": with_today, "stand_vom": rt.ledger.as_of.isoformat(), "geschaetzt": estimated}


@dataclass(frozen=True, kw_only=True)
class TimebutlerSensorDescription(SensorEntityDescription):
    """Sensor description with its data sources and value functions."""

    sources: tuple[str, ...]
    value_fn: ValueFn
    attrs_fn: Callable[[TimebutlerRuntime], dict[str, Any] | None] | None = None


def _minutes(key: str, sources: tuple[str, ...], value_fn: ValueFn, attrs_fn=None) -> TimebutlerSensorDescription:
    return TimebutlerSensorDescription(
        key=key,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_display_precision=0,
        sources=sources,
        value_fn=value_fn,
        attrs_fn=attrs_fn,
    )


def _days_sensor(key: str, value_fn: ValueFn, attrs_fn=None) -> TimebutlerSensorDescription:
    return TimebutlerSensorDescription(
        key=key,
        native_unit_of_measurement=UnitOfTime.DAYS,
        suggested_display_precision=1,
        sources=("background",),
        value_fn=value_fn,
        attrs_fn=attrs_fn,
    )


SENSORS: tuple[TimebutlerSensorDescription, ...] = (
    TimebutlerSensorDescription(
        key="stempeluhr",
        device_class=SensorDeviceClass.ENUM,
        options=list(CLOCK_STATES),
        sources=("live",),
        value_fn=_clock_state,
        attrs_fn=_clock_attrs,
    ),
    _minutes("arbeitszeit_heute", ("live",), _today_field("worked")),
    _minutes("soll_heute", ("live",), _today_field("commitment")),
    _minutes("rest_heute", ("live",), _today_field("remaining")),
    _minutes("pause_heute", ("live",), _today_field("break_minutes")),
    TimebutlerSensorDescription(
        key="fortschritt_heute",
        native_unit_of_measurement=PERCENTAGE,
        sources=("live",),
        value_fn=_today_field("percent"),
    ),
    _minutes("arbeitszeit_woche", ("background", "live"), _total(_week), _week_attrs),
    _minutes("arbeitszeit_monat", ("background", "live"), _total(_month), _month_attrs),
    _days_sensor("urlaub_rest", _time_off("vacationDaysRemaining"), _vacation_attrs),
    _days_sensor("krankheitstage", _time_off("illnessDaysTaken")),
    TimebutlerSensorDescription(
        key="naechste_abwesenheit",
        device_class=SensorDeviceClass.DATE,
        sources=("background",),
        value_fn=_next_absence,
        attrs_fn=_next_absence_attrs,
    ),
    TimebutlerSensorDescription(
        key="ueberstunden",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        sources=("background", "live"),
        value_fn=_overtime_value,
        attrs_fn=_overtime_attrs,
    ),
)


class TimebutlerSensor(TimebutlerEntity, SensorEntity):
    """A Timebutler value described by a TimebutlerSensorDescription."""

    _platform_domain = "sensor"
    # Day maps and today's live balance change every minute while clocked in;
    # keep them out of history.
    _unrecorded_attributes = frozenset({"tage", "liste", "mit_heute", "geschaetzt"})
    entity_description: TimebutlerSensorDescription

    def __init__(
        self,
        entry: TimebutlerConfigEntry,
        runtime: TimebutlerRuntime,
        description: TimebutlerSensorDescription,
    ) -> None:
        super().__init__(entry, runtime, description.key, description.sources)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self._runtime)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        attrs_fn = self.entity_description.attrs_fn
        return attrs_fn(self._runtime) if attrs_fn else None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TimebutlerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add all Timebutler sensors."""
    async_add_entities(TimebutlerSensor(entry, entry.runtime_data, description) for description in SENSORS)
