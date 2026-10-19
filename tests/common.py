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
}

ENTRIES_PATH = "/time-entries/my"
# Fixture (or entry list) per (year, month) that /time-entries/my answers by default; any other month is empty.
DEFAULT_ENTRIES: dict[tuple[int, int], str | list[dict[str, Any]]] = {(2026, 10): "entries_2026_10"}


def load_fixture_json(name: str) -> Any:
    """Read tests/fixtures/<name>.json."""
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def mock_api(
    aioclient_mock,
    overrides: dict[str, str | dict] | None = None,
    entries: dict[tuple[int, int], str | list[dict[str, Any]]] | None = None,
) -> None:
    """Answer every endpoint the integration uses.

    An override maps an API path to a fixture name or to keyword arguments
    for aioclient_mock (e.g. {"status": 401} or {"exc": TimeoutError()}).
    /time-entries/my answers per month: `entries` maps (year, month) to a
    fixture name or an entry list (default: October 2026 from the fixture),
    every other month is empty. An override for the path answers every month.
    """
    overrides = overrides or {}
    for path, default in DEFAULT_RESPONSES.items():
        spec = overrides.get(path, default)
        kwargs = spec if isinstance(spec, dict) else {"json": load_fixture_json(spec)}
        aioclient_mock.get(f"{API_BASE}{path}", **kwargs)
    url = f"{API_BASE}{ENTRIES_PATH}"
    if ENTRIES_PATH in overrides:
        spec = overrides[ENTRIES_PATH]
        aioclient_mock.get(url, **(spec if isinstance(spec, dict) else {"json": load_fixture_json(spec)}))
        return
    # The first matching mock wins: the specific months before the catch-all.
    for (year, month), spec in (DEFAULT_ENTRIES if entries is None else entries).items():
        data = load_fixture_json(spec) if isinstance(spec, str) else spec
        aioclient_mock.get(url, params={"year": year, "month": month}, json=data)
    aioclient_mock.get(url, json=[])


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


def entry_dto(day: str, minutes: int, status: str = "done") -> dict[str, Any]:
    """A minimal time entry as /time-entries/my returns it."""
    return {"id": f"{day}-{minutes}", "date": day, "workedMinutes": minutes, "status": status}


def absence_dto(
    kind: str,
    start: str,
    end: str | None = None,
    *,
    status: str = "approved",
    half_day: str | None = None,
    hours: float | str | None = None,
) -> dict[str, Any]:
    """A minimal absence request as /absence-requests/my returns it."""
    return {
        "id": f"{kind}-{start}",
        "type": kind,
        "startDate": start,
        "endDate": end or start,
        "status": status,
        "halfDay": half_day,
        "hours": hours,
    }
