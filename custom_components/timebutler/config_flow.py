"""Config flow for the Timebutler integration."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_API_TOKEN
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    DateSelector,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util import dt as dt_util

from .api import TimebutlerAuthError, TimebutlerClient, TimebutlerConnectionError
from .const import DOMAIN, OPT_AS_OF, OPT_BALANCE_MINUTES
from .model import format_balance, parse_balance

_LOGGER = logging.getLogger(__name__)

TOKEN_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_API_TOKEN): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        )
    }
)

CONF_BALANCE = "balance"

OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_BALANCE): TextSelector(),
        vol.Required(OPT_AS_OF): DateSelector(),
    }
)


class TimebutlerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up Timebutler with a personal access token."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> TimebutlerOptionsFlow:
        """Options: the overtime balance start value."""
        return TimebutlerOptionsFlow()

    async def _async_validate(self, token: str) -> tuple[dict[str, Any] | None, dict[str, str]]:
        """Return the profile for the token, or the form errors."""
        client = TimebutlerClient(async_get_clientsession(self.hass), token)
        try:
            profile = await client.get_profile()
        except TimebutlerAuthError:
            return None, {"base": "invalid_auth"}
        except TimebutlerConnectionError:
            return None, {"base": "cannot_connect"}
        except Exception:  # noqa: BLE001 - shown as "unknown" in the form
            _LOGGER.exception("Unexpected error while checking the Timebutler token")
            return None, {"base": "unknown"}
        return profile, {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the token."""
        errors: dict[str, str] = {}
        if user_input is not None:
            profile, errors = await self._async_validate(user_input[CONF_API_TOKEN])
            if profile is not None:
                await self.async_set_unique_id(str(profile["id"]))
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=profile.get("name") or "Timebutler",
                    data={CONF_API_TOKEN: user_input[CONF_API_TOKEN]},
                )
        return self.async_show_form(step_id="user", data_schema=TOKEN_SCHEMA, errors=errors)

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauth after the stored token was rejected."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new token of the same account."""
        errors: dict[str, str] = {}
        if user_input is not None:
            profile, errors = await self._async_validate(user_input[CONF_API_TOKEN])
            if profile is not None:
                await self.async_set_unique_id(str(profile["id"]))
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(),
                    data_updates={CONF_API_TOKEN: user_input[CONF_API_TOKEN]},
                )
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=TOKEN_SCHEMA, errors=errors
        )


class TimebutlerOptionsFlow(OptionsFlow):
    """Set the overtime balance and the day it is valid for."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for balance and date; prefill the current values."""
        errors: dict[str, str] = {}
        if user_input is not None:
            minutes = 0
            try:
                minutes = parse_balance(user_input[CONF_BALANCE])
            except ValueError:
                errors[CONF_BALANCE] = "invalid_balance"
            as_of = date.fromisoformat(user_input[OPT_AS_OF])
            if as_of >= dt_util.now().date():
                errors[OPT_AS_OF] = "as_of_not_past"
            if not errors:
                return self.async_create_entry(
                    data={OPT_BALANCE_MINUTES: minutes, OPT_AS_OF: as_of.isoformat()}
                )
            suggested = user_input
        else:
            options = self.config_entry.options
            yesterday = dt_util.now().date() - timedelta(days=1)
            suggested = {OPT_AS_OF: options.get(OPT_AS_OF, yesterday.isoformat())}
            if OPT_BALANCE_MINUTES in options:
                suggested[CONF_BALANCE] = format_balance(options[OPT_BALANCE_MINUTES])
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(OPTIONS_SCHEMA, suggested),
            errors=errors,
        )
