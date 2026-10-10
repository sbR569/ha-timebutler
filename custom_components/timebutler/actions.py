"""Actions on Timebutler, shared by buttons and services."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from datetime import date, time
import logging
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util import dt as dt_util

from .api import (
    TimebutlerAuthError,
    TimebutlerError,
    TimebutlerRequestError,
)
from .const import DOMAIN, EVENT_BREAK_REGULATION
from .coordinator import LiveData, TimebutlerConfigEntry
from .model import (
    InputError,
    absence_body,
    absence_type_code,
    assignment_id,
    break_regulation_message,
    find_absence,
    find_time_entry,
    match_item,
    parse_clock,
    stop_result,
    time_entry_body,
)

_LOGGER = logging.getLogger(__name__)

_CLOCK_REFUSALS = {400: "clock_disabled", 403: "terminal_only"}
_TRIP_REFUSALS = {400: "business_trip_disabled", 403: "terminal_only", 409: "business_trip_conflict"}
CLOCK_ERRORS: dict[str, dict[int, str]] = {
    "start": {**_CLOCK_REFUSALS, 409: "clock_start_conflict"},
    "pause": {**_CLOCK_REFUSALS, 409: "clock_not_running"},
    "resume": {**_CLOCK_REFUSALS, 409: "clock_not_paused"},
    "cancel": {**_CLOCK_REFUSALS, 409: "clock_already_idle"},
    "business_trip_start": _TRIP_REFUSALS,
    "business_trip_end": _TRIP_REFUSALS,
}
STOP_ERRORS = {400: "invalid_assignment", 403: "terminal_only", 409: "clock_stop_conflict"}


def _error(key: str, **placeholders: str) -> HomeAssistantError:
    return HomeAssistantError(
        translation_domain=DOMAIN, translation_key=key, translation_placeholders=placeholders or None
    )


@contextmanager
def checked_input() -> Iterator[None]:
    """Report InputError from the model as a translated validation error."""
    try:
        yield
    except InputError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key=err.key,
            translation_placeholders=err.placeholders or None,
        ) from err


async def call_api[T](
    hass: HomeAssistant,
    entry: TimebutlerConfigEntry,
    request: Awaitable[T],
    errors: dict[int, str],
) -> T:
    """Await one API call; client errors become translated HA errors."""
    try:
        return await request
    except TimebutlerAuthError as err:
        entry.async_start_reauth(hass)
        raise _error("auth_failed") from err
    except TimebutlerRequestError as err:
        _LOGGER.debug("Timebutler refused a request: %s", err)
        if err.status in errors:
            raise _error(errors[err.status]) from err
        raise _error("request_rejected", status=str(err.status)) from err
    except TimebutlerError as err:
        raise _error("connection_error") from err


def _notification_id(entry: TimebutlerConfigEntry) -> str:
    return f"{DOMAIN}_break_regulation_{entry.entry_id}"


def _push_clock(entry: TimebutlerConfigEntry, status: dict[str, Any]) -> None:
    """Show the new clock state at once instead of waiting for the next poll."""
    live = entry.runtime_data.live
    today = live.data.today if live.data else None
    live.async_set_updated_data(LiveData(clock=parse_clock(status), today=today))


async def async_clock_action(hass: HomeAssistant, entry: TimebutlerConfigEntry, action: str) -> None:
    """Start, pause, resume or cancel the clock, or start/end a business trip."""
    runtime = entry.runtime_data
    status = await call_api(hass, entry, runtime.live.client.clock(action), CLOCK_ERRORS[action])
    _push_clock(entry, status)
    if action == "cancel":
        persistent_notification.async_dismiss(hass, _notification_id(entry))
    await runtime.live.async_request_refresh()


async def _assignment(
    hass: HomeAssistant,
    entry: TimebutlerConfigEntry,
    value: str | None,
    fetch: Callable[[], Awaitable[dict[str, Any]]],
    list_key: str,
    default_key: str,
    unknown_key: str,
) -> int | None:
    """ID to send for a project/category; None leaves the field out."""
    if value:
        response = await call_api(hass, entry, fetch(), {})
        with checked_input():
            return assignment_id(value, response.get(list_key) or [], None, unknown_key)
    try:
        response = await fetch()
    except TimebutlerError as err:
        # Only the default is missing; the action itself must still work.
        _LOGGER.debug("No %s default from Timebutler: %s", list_key, err)
        return None
    return assignment_id(None, [], response.get(default_key), unknown_key)


async def async_assignments(
    hass: HomeAssistant, entry: TimebutlerConfigEntry, project: str | None, category: str | None
) -> dict[str, int]:
    """projectId/categoryId fields for a new time entry."""
    client = entry.runtime_data.live.client
    body: dict[str, int] = {}
    project_id = await _assignment(
        hass, entry, project, client.get_projects, "projects", "defaultProjectId", "unknown_project"
    )
    if project_id is not None:
        body["projectId"] = project_id
    category_id = await _assignment(
        hass, entry, category, client.get_categories, "categories", "defaultCategoryId", "unknown_category"
    )
    if category_id is not None:
        body["categoryId"] = category_id
    return body


def _report_break_regulation(hass: HomeAssistant, entry: TimebutlerConfigEntry, info: dict[str, Any]) -> None:
    hass.bus.async_fire(
        EVENT_BREAK_REGULATION,
        {
            "config_entry_id": entry.entry_id,
            "detail": info.get("detailText"),
            "obligatory": bool(info.get("obligatory")),
            "has_no_remedy": bool(info.get("hasNoRemedy")),
            "correction_work_minutes": info.get("correctionWorkMinutes"),
            "correction_break_minutes": info.get("correctionBreakMinutes"),
            "resulting_work_minutes": info.get("resultingWorkMinutes"),
            "resulting_break_minutes": info.get("resultingBreakMinutes"),
        },
    )
    title, message = break_regulation_message(info, hass.config.language)
    persistent_notification.async_create(hass, message, title=title, notification_id=_notification_id(entry))


async def async_stop(
    hass: HomeAssistant,
    entry: TimebutlerConfigEntry,
    *,
    project: str | None = None,
    category: str | None = None,
    remarks: str | None = None,
    break_regulation: str | None = None,
) -> dict[str, Any]:
    """Stop and save the clock; asks back when the break regulation is violated."""
    runtime = entry.runtime_data
    body: dict[str, Any] = await async_assignments(hass, entry, project, category)
    if remarks:
        body["remarks"] = remarks
    if break_regulation:
        body["breakRegulation"] = break_regulation
    response = await call_api(hass, entry, runtime.live.client.clock_stop(body), STOP_ERRORS)
    if response is not None and response.get("outcome") == "confirmationRequired":
        info = response.get("breakRegulation") or {}
        _report_break_regulation(hass, entry, info)
        raise _error("break_regulation_confirmation", detail=str(info.get("detailText") or ""))
    persistent_notification.async_dismiss(hass, _notification_id(entry))
    _push_clock(entry, {"status": "idle"})
    await runtime.live.async_request_refresh()
    # The pushed state hides the clock-out from the live coordinator, so the
    # week/month refresh it would trigger is requested here.
    await runtime.background.async_request_refresh()
    return stop_result(response)


ABSENCE_ERRORS = {400: "absence_invalid", 403: "absence_type_forbidden", 409: "absence_overlap"}
DELETE_ABSENCE_ERRORS = {
    400: "absence_reason_required",
    403: "absence_delete_forbidden",
    404: "absence_unknown_id",
}


async def async_request_absence(
    hass: HomeAssistant,
    entry: TimebutlerConfigEntry,
    *,
    absence_type: str,
    start: date,
    end: date | None = None,
    half_day: str | None = None,
    hours: float | None = None,
    comments: str | None = None,
    substitute: str | None = None,
) -> dict[str, Any]:
    """Submit an absence request for the user."""
    runtime = entry.runtime_data
    client = runtime.live.client
    types = runtime.background.data.absence_types if runtime.background.data else {}
    if not types:
        types = {
            str(item.get("id")): str(item.get("name"))
            for item in await call_api(hass, entry, client.get_absence_types(), {})
        }
    with checked_input():
        body = absence_body(absence_type_code(absence_type, types), start, end, half_day, hours, comments)
    if substitute:
        substitutes = await call_api(hass, entry, client.get_substitutes(), {})
        with checked_input():
            body["substituteEmployeeId"] = match_item(substitute, substitutes, "unknown_substitute")
    created = await call_api(hass, entry, client.create_absence(body), ABSENCE_ERRORS)
    await runtime.background.async_request_refresh()
    return {"id": created.get("id"), "status": created.get("status"), "tage": created.get("days")}


async def async_delete_absence(
    hass: HomeAssistant,
    entry: TimebutlerConfigEntry,
    *,
    absence_id: str | None = None,
    day: date | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Delete an absence request, or ask for its cancellation."""
    runtime = entry.runtime_data
    client = runtime.live.client
    if absence_id is None:
        assert day is not None  # the service schema requires id or date
        requests = await call_api(hass, entry, client.get_absence_requests(), {})
        with checked_input():
            absence_id = find_absence(requests, day)
    result = await call_api(hass, entry, client.delete_absence(absence_id, reason), DELETE_ABSENCE_ERRORS)
    await runtime.background.async_request_refresh()
    return {"id": result.get("id") or absence_id, "result": result.get("result")}


ADD_TIME_ENTRY_ERRORS = {400: "time_entry_invalid", 403: "time_entry_forbidden", 422: "remarks_too_long"}
DELETE_TIME_ENTRY_ERRORS = {
    403: "time_entry_delete_forbidden",
    404: "time_entry_unknown_id",
    409: "month_closed",
}


async def _refresh_all(entry: TimebutlerConfigEntry) -> None:
    await entry.runtime_data.live.async_request_refresh()
    await entry.runtime_data.background.async_request_refresh()


async def async_add_time_entry(
    hass: HomeAssistant,
    entry: TimebutlerConfigEntry,
    *,
    day: date,
    start: time,
    end: time,
    break_minutes: int = 0,
    project: str | None = None,
    category: str | None = None,
    remarks: str | None = None,
) -> dict[str, Any]:
    """Add a completed time entry, times in Home Assistant's time zone."""
    with checked_input():
        body = time_entry_body(day, start, end, break_minutes, remarks, dt_util.get_default_time_zone())
    body.update(await async_assignments(hass, entry, project, category))
    client = entry.runtime_data.live.client
    created = await call_api(hass, entry, client.add_clock_entry(body), ADD_TIME_ENTRY_ERRORS)
    await _refresh_all(entry)
    return {
        "id": created.get("id"),
        "gearbeitet": created.get("workedMinutes"),
        "pause": created.get("breakMinutes"),
    }


async def async_delete_time_entry(
    hass: HomeAssistant,
    entry: TimebutlerConfigEntry,
    *,
    time_entry_id: str | None = None,
    day: date | None = None,
    start: time | None = None,
) -> dict[str, Any]:
    """Delete a time entry, or ask for its deletion."""
    client = entry.runtime_data.live.client
    if time_entry_id is None:
        assert day is not None  # the service schema requires id or date
        entries = await call_api(hass, entry, client.get_time_entries(day.year, day.month), {})
        with checked_input():
            time_entry_id = find_time_entry(entries, day, start)
    result = await call_api(hass, entry, client.delete_time_entry(time_entry_id), DELETE_TIME_ENTRY_ERRORS)
    await _refresh_all(entry)
    return {"id": result.get("id") or time_entry_id, "result": result.get("result")}
