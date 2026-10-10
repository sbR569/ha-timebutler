"""Helpers shared by the tests: JSON fixtures and a mocked Timebutler API."""
import json
from pathlib import Path
from typing import Any

from custom_components.timebutler.const import API_BASE

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
