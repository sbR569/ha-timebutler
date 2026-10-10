"""Services of the Timebutler integration."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .actions import async_clock_action, async_delete_absence, async_request_absence, async_stop
from .const import DOMAIN
from .coordinator import TimebutlerConfigEntry

ATTR_CONFIG_ENTRY_ID = "config_entry_id"

BASE_SCHEMA = vol.Schema({vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string})
CLOCK_SERVICES = ("start", "pause", "resume", "cancel", "business_trip_start", "business_trip_end")
STOP_SCHEMA = BASE_SCHEMA.extend(
    {
        vol.Optional("project"): cv.string,
        vol.Optional("category"): cv.string,
        vol.Optional("remarks"): cv.string,
        vol.Optional("break_regulation"): vol.In(["apply", "ignore"]),
    }
)
REQUEST_ABSENCE_SCHEMA = BASE_SCHEMA.extend(
    {
        vol.Required("type"): cv.string,
        vol.Required("start_date"): cv.date,
        vol.Optional("end_date"): cv.date,
        vol.Optional("half_day"): vol.In(["morning", "afternoon"]),
        vol.Optional("hours"): vol.Coerce(float),
        vol.Optional("comments"): cv.string,
        vol.Optional("substitute"): cv.string,
    }
)
DELETE_ABSENCE_SCHEMA = vol.All(
    BASE_SCHEMA.extend(
        {
            vol.Optional("id"): cv.string,
            vol.Optional("date"): cv.date,
            vol.Optional("reason"): cv.string,
        }
    ),
    cv.has_at_least_one_key("id", "date"),
    cv.has_at_most_one_key("id", "date"),
)


def _invalid(key: str) -> ServiceValidationError:
    return ServiceValidationError(translation_domain=DOMAIN, translation_key=key)


def resolve_entry(hass: HomeAssistant, call: ServiceCall) -> TimebutlerConfigEntry:
    """The loaded entry named in the call, or the only loaded one."""
    entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID)
    if entry_id:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            raise _invalid("entry_not_found")
        if entry.state is not ConfigEntryState.LOADED:
            raise _invalid("entry_not_loaded")
        return entry
    loaded = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]
    if len(loaded) != 1:
        raise _invalid("entry_required")
    return loaded[0]


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the Timebutler services."""

    def clock_handler(action: str) -> Callable[[ServiceCall], Coroutine[Any, Any, None]]:
        async def handle(call: ServiceCall) -> None:
            await async_clock_action(hass, resolve_entry(hass, call), action)

        return handle

    for action in CLOCK_SERVICES:
        hass.services.async_register(DOMAIN, action, clock_handler(action), schema=BASE_SCHEMA)

    async def handle_stop(call: ServiceCall) -> ServiceResponse:
        return await async_stop(
            hass,
            resolve_entry(hass, call),
            project=call.data.get("project"),
            category=call.data.get("category"),
            remarks=call.data.get("remarks"),
            break_regulation=call.data.get("break_regulation"),
        )

    hass.services.async_register(
        DOMAIN, "stop", handle_stop, schema=STOP_SCHEMA, supports_response=SupportsResponse.OPTIONAL
    )

    async def handle_request_absence(call: ServiceCall) -> ServiceResponse:
        return await async_request_absence(
            hass,
            resolve_entry(hass, call),
            absence_type=call.data["type"],
            start=call.data["start_date"],
            end=call.data.get("end_date"),
            half_day=call.data.get("half_day"),
            hours=call.data.get("hours"),
            comments=call.data.get("comments"),
            substitute=call.data.get("substitute"),
        )

    async def handle_delete_absence(call: ServiceCall) -> ServiceResponse:
        return await async_delete_absence(
            hass,
            resolve_entry(hass, call),
            absence_id=call.data.get("id"),
            day=call.data.get("date"),
            reason=call.data.get("reason"),
        )

    hass.services.async_register(
        DOMAIN,
        "request_absence",
        handle_request_absence,
        schema=REQUEST_ABSENCE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "delete_absence",
        handle_delete_absence,
        schema=DELETE_ABSENCE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
