"""Config flow: token check, duplicates and reauth."""
from unittest.mock import patch

import aiohttp
import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_API_TOKEN
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.timebutler.const import API_BASE, DOMAIN
from tests.common import load_fixture_json

pytestmark = pytest.mark.usefixtures("auto_enable_custom_integrations")

PROFILE_URL = f"{API_BASE}/user/profile"


@pytest.fixture(autouse=True)
def _no_entry_setup():
    with patch("custom_components.timebutler.async_setup_entry", return_value=True, create=True):
        yield


async def _submit_token(hass, token="tb_test"):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_TOKEN: token})


async def test_user_flow_creates_entry(hass, aioclient_mock):
    aioclient_mock.get(PROFILE_URL, json=load_fixture_json("profile"))

    result = await _submit_token(hass)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Max Mustermann"
    assert result["data"] == {CONF_API_TOKEN: "tb_test"}
    assert result["result"].unique_id == "4711"
    assert aioclient_mock.mock_calls[0][3]["Authorization"] == "Bearer tb_test"


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"status": 401}, "invalid_auth"),
        ({"exc": aiohttp.ClientConnectionError()}, "cannot_connect"),
    ],
)
async def test_user_flow_shows_errors(hass, aioclient_mock, kwargs, error):
    aioclient_mock.get(PROFILE_URL, **kwargs)

    result = await _submit_token(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}


async def test_user_flow_unknown_error(hass):
    with patch(
        "custom_components.timebutler.config_flow.TimebutlerClient.get_profile",
        side_effect=RuntimeError("boom"),
    ):
        result = await _submit_token(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "unknown"}


async def test_user_flow_aborts_when_already_configured(hass, aioclient_mock):
    MockConfigEntry(domain=DOMAIN, unique_id="4711", data={CONF_API_TOKEN: "tb_old"}).add_to_hass(hass)
    aioclient_mock.get(PROFILE_URL, json=load_fixture_json("profile"))

    result = await _submit_token(hass)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_updates_token(hass, aioclient_mock):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="4711", data={CONF_API_TOKEN: "tb_old"})
    entry.add_to_hass(hass)
    aioclient_mock.get(PROFILE_URL, json=load_fixture_json("profile"))

    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_TOKEN: "tb_new"})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_API_TOKEN] == "tb_new"


async def test_reauth_rejects_token_of_other_account(hass, aioclient_mock):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="4711", data={CONF_API_TOKEN: "tb_old"})
    entry.add_to_hass(hass)
    aioclient_mock.get(PROFILE_URL, json={**load_fixture_json("profile"), "id": "9999"})

    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_TOKEN: "tb_other"})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert entry.data[CONF_API_TOKEN] == "tb_old"
