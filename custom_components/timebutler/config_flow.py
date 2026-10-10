"""Config flow for the Timebutler integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_API_TOKEN
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import TimebutlerAuthError, TimebutlerClient, TimebutlerConnectionError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

TOKEN_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_API_TOKEN): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        )
    }
)


class TimebutlerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up Timebutler with a personal access token."""

    VERSION = 1

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
