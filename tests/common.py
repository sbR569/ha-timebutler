"""Helpers shared by the tests: JSON fixtures and a mocked Timebutler API."""
import json
from pathlib import Path
from typing import Any

from homeassistant.const import CONF_API_TOKEN
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.timebutler.const import API_BASE, DOMAIN

FIXTURES = Path(__file__).parent / "fixtures"

DEFAULT_RESPONSES = {
    "/user/profile": "profile",
    "/time-clock/status": "clock_running",
    "/time-tracking/work-summary": "work_summary",
    "/time-tracking/time-off-summary": "time_off",
    "/absence-requests/my": "absences",
    "/absence-types": "absence_types",
    "/time-entries/my": "entries_2026_10",
}


def load_fixture_json(name: str) -> Any:
    """Read tests/fixtures/<name>.json."""
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def mock_api(aioclient_mock, overrides: dict[str, str | dict] | None = None) -> None:
    """Answer every endpoint the integration uses.

    An override maps an API path to a fixture name or to keyword arguments
    for aioclient_mock (e.g. {"status": 401} or {"exc": TimeoutError()}).
    """
    overrides = overrides or {}
    for path, default in DEFAULT_RESPONSES.items():
        spec = overrides.get(path, default)
        kwargs = spec if isinstance(spec, dict) else {"json": load_fixture_json(spec)}
        aioclient_mock.get(f"{API_BASE}{path}", **kwargs)


def remock(aioclient_mock, overrides: dict[str, str | dict] | None = None) -> None:
    """Forget earlier mocks and recorded calls, then answer reads again."""
    aioclient_mock.clear_requests()
    mock_api(aioclient_mock, overrides)


def api_calls(aioclient_mock, method: str, path: str) -> list:
    """Bodies of the recorded calls to one endpoint, query string ignored."""
    url = f"{API_BASE}{path}"
    return [
        data
        for call_method, call_url, data, _ in aioclient_mock.mock_calls
        if call_method.upper() == method and str(call_url).split("?")[0] == url
    ]


async def setup_integration(hass, aioclient_mock, overrides=None) -> MockConfigEntry:
    """Set up one Timebutler entry against the mocked API."""
    mock_api(aioclient_mock, overrides)
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="4711", title="Max Mustermann", data={CONF_API_TOKEN: "tb_test"}
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry
