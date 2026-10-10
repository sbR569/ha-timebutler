# Timebutler Interaction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the Timebutler integration act on Timebutler: operate the time clock (buttons and services), request and delete absences, add and delete time entries.

**Architecture:** `api.py` gains a generic `_request` with write-call error mapping. Pure input handling (name/ID resolution, validation, date matching, timestamps, notification text) goes into `model.py` (no HA imports). A new `actions.py` runs one action for a config entry, maps client errors to translated HA errors and updates the coordinators; `button.py` and `services.py` are thin callers of `actions.py`.

**Deviations from the spec (implementation detail, same behavior):** one `TimebutlerRequestError` carrying `status` replaces the per-status error classes the spec names; after a saved stop the background refresh is requested explicitly by `actions.async_stop`, because pushing the idle state locally hides the clock-out from the live coordinator's own transition check.

**Tech Stack:** Home Assistant custom integration (Python 3.13+, aiohttp, voluptuous), pytest + pytest-homeassistant-custom-component with `aioclient_mock`.

**Spec:** `docs/superpowers/specs/2026-10-10-timebutler-interaction-design.md`

## Global Constraints

- Home Assistant floor stays `2025.3.0` (`hacs.json`); do not use APIs newer than that (e.g. use `hass.config_entries.async_entries` + state check, not `async_loaded_entries`).
- `manifest.json` `requirements` stays `[]`.
- `model.py` must not import Home Assistant.
- Entity IDs are fixed and German: `button.timebutler_<key>` with keys `start`, `pause`, `fortsetzen`, `stopp`, `stopp_pausenkorrektur`, `verwerfen`, `dienstreise_start`, `dienstreise_ende`.
- Service names: `start`, `pause`, `resume`, `stop`, `cancel`, `business_trip_start`, `business_trip_end`, `request_absence`, `delete_absence`, `add_time_entry`, `delete_time_entry`; every service has optional `config_entry_id`.
- Service response keys: stop → `outcome`, `id`, `gearbeitet`, `pause`; request_absence → `id`, `status`, `tage`; delete_* → `id`, `result`; add_time_entry → `id`, `gearbeitet`, `pause`.
- Event name: `timebutler_break_regulation`. Notification id: `timebutler_break_regulation_<entry_id>`.
- A 403 on a **write** call must never start reauth; only 401 does.
- `strings.json` and `translations/en.json` stay identical; `translations/de.json` has the same keys in German.
- Every new user-facing error is a translated `HomeAssistantError` / `ServiceValidationError` (`translation_domain="timebutler"`).
- Version becomes `0.2.0` in `const.VERSION` and `manifest.json` (Task 5).

## How to run tests

Home Assistant does not run on Windows. From the worktree, run tests in WSL with the existing venv:

```bash
MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu -- bash -lc 'cd "/mnt/c/Users/begiers/GitProjects/ha-timebutler/.claude/worktrees/timebutler-integration-353207" && ~/.venvs/ha-zyxel-test/bin/python -m pytest -q -p no:cacheprovider tests/test_api.py'
```

Steps below write only the pytest arguments (e.g. "Run: `pytest tests/test_api.py -v`"); always run them through the WSL command above, replacing the trailing `tests/test_api.py` with the given arguments. Baseline before Task 1: `64 passed`.

## Review Focus

1. **Stopping must work even when `GET /projects` or `GET /categories` fails** (projects disabled, outage): without `project`/`category` the stop is sent without those fields. Test: `test_stop_still_works_without_project_list` (Task 3).
2. **"Terminal only" (403 on a write) must not trigger a reauth flow**, otherwise the user is asked for a new token that would not help. Tests: `test_write_errors_carry_status` (Task 1), `test_terminal_only_does_not_start_reauth` (Task 3).
3. **After clock-out, week/month must refresh even though the clock state was pushed locally first** (the coordinator's own idle-transition detection no longer sees the change). Test: `test_stop_sends_company_defaults_and_returns_result` asserts a `GET /time-entries/my` after stop (Task 3).
4. **Times entered on a DST change day** must use the real duration for the break check and timestamps. Test: `test_time_entry_body_on_dst_change_day` (Task 8).
5. **User-supplied IDs in DELETE paths** must be URL-quoted so `"1/2"` cannot address another endpoint. Tests: `test_delete_absence_quotes_the_id` (Task 6), `test_delete_time_entry_quotes_the_id` (Task 8).

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `custom_components/timebutler/api.py` | modify | `_request` with read/write error mapping, `TimebutlerRequestError(status)`, write + lookup methods |
| `custom_components/timebutler/model.py` | modify | `InputError`, `match_item`, `assignment_id`, `stop_result`, `break_regulation_message`, `absence_type_code`, `absence_body`, `find_absence`, `local_timestamp_ms`, `time_entry_body`, `find_time_entry`; `upcoming_absences` adds `id` |
| `custom_components/timebutler/const.py` | modify | `EVENT_BREAK_REGULATION`, `VERSION = "0.2.0"` |
| `custom_components/timebutler/actions.py` | create | One function per action; error translation; coordinator updates; break-regulation notification/event |
| `custom_components/timebutler/services.py` | create | Schemas, config entry selection, registration |
| `custom_components/timebutler/services.yaml` | create | Service field selectors |
| `custom_components/timebutler/button.py` | create | Time clock buttons |
| `custom_components/timebutler/__init__.py` | modify | `CONFIG_SCHEMA`, `async_setup` registering services, `Platform.BUTTON` |
| `custom_components/timebutler/strings.json`, `translations/en.json`, `translations/de.json` | modify | Button names, services, selectors, exceptions |
| `custom_components/timebutler/manifest.json` | modify | version |
| `tests/common.py` | modify | `setup_integration`, `remock`, `api_calls` |
| `tests/fixtures/*.json` | create | `clock_paused`, `stop_saved`, `stop_confirmation`, `projects`, `categories`, `substitutes`, `absence_created`, `delete_result`, `clock_entry` |
| `tests/test_api.py`, `tests/test_model.py`, `tests/test_init.py` | modify | New cases |
| `tests/test_services.py`, `tests/test_button.py` | create | Services and buttons |
| `README.md`, `dashboards/clock-buttons.yaml` | modify / create | Docs |

---

# Phase 1 – Time clock

### Task 1: API write path and clock calls

**Files:**
- Modify: `custom_components/timebutler/api.py`
- Create: `tests/fixtures/projects.json`, `tests/fixtures/categories.json`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `class TimebutlerRequestError(TimebutlerError)` with attribute `status: int` (400, 403, 404, 409 or 422 on write calls).
  - `TimebutlerClient.clock(action: str) -> dict[str, Any]` for `action` in `CLOCK_PATHS` keys `"start" | "pause" | "resume" | "cancel" | "business_trip_start" | "business_trip_end"`; returns the `TimeClockStatusDto`.
  - `TimebutlerClient.clock_stop(body: dict[str, Any]) -> dict[str, Any] | None` (`None` on HTTP 204).
  - `TimebutlerClient.get_projects() -> dict[str, Any]`, `TimebutlerClient.get_categories() -> dict[str, Any]`.
  - Internal: `_request(method, path, *, params=None, json=None, time_tracking=False, write=False)`, `_write(method, path, json=None)`.

- [ ] **Step 1: Add fixtures**

`tests/fixtures/projects.json`:
```json
{"projects": [{"id": "5", "name": "Kundenprojekt A", "isFavorite": true}, {"id": "6", "name": "Intern", "isFavorite": false}], "defaultProjectId": "5", "isProjectMandatory": false}
```

`tests/fixtures/categories.json`:
```json
{"categories": [{"id": "7", "name": "Entwicklung"}, {"id": "8", "name": "Meeting"}], "defaultCategoryId": null, "isCategoryMandatory": false}
```

- [ ] **Step 2: Write the failing tests**

In `tests/test_api.py`, extend the import block to:

```python
from custom_components.timebutler.api import (
    TimebutlerAuthError,
    TimebutlerClient,
    TimebutlerConnectionError,
    TimebutlerRequestError,
    TimebutlerTimeTrackingDisabled,
)
from custom_components.timebutler.const import API_BASE, USER_AGENT
from tests.common import load_fixture_json
```

Append:

```python
@pytest.mark.parametrize(
    ("action", "path"),
    [
        ("start", "/time-clock/start"),
        ("pause", "/time-clock/pause"),
        ("resume", "/time-clock/resume"),
        ("cancel", "/time-clock/cancel"),
        ("business_trip_start", "/time-clock/business-trip-start"),
        ("business_trip_end", "/time-clock/business-trip-end"),
    ],
)
async def test_clock_actions_post_without_body(client, aioclient_mock, action, path):
    aioclient_mock.post(f"{API_BASE}{path}", json={"status": "running"})

    assert await client.clock(action) == {"status": "running"}

    method, url, data, headers = aioclient_mock.mock_calls[0]
    assert method.upper() == "POST"
    assert str(url) == f"{API_BASE}{path}"
    assert data is None
    assert headers["Authorization"] == "Bearer tb_secret"


async def test_stop_sends_body(client, aioclient_mock):
    aioclient_mock.post(f"{API_BASE}/time-clock/stop", json={"outcome": "saved"})

    assert await client.clock_stop({"projectId": 5, "remarks": "Review"}) == {"outcome": "saved"}

    _, _, data, _ = aioclient_mock.mock_calls[0]
    assert data == {"projectId": 5, "remarks": "Review"}


async def test_stop_of_waiting_clock_returns_none(client, aioclient_mock):
    aioclient_mock.post(f"{API_BASE}/time-clock/stop", status=204)

    assert await client.clock_stop({}) is None


@pytest.mark.parametrize("status", [400, 403, 404, 409, 422])
async def test_write_errors_carry_status(client, aioclient_mock, status):
    # 403 on a write means "not allowed here" (e.g. terminal only), not a bad token.
    aioclient_mock.post(f"{API_BASE}/time-clock/pause", status=status)

    with pytest.raises(TimebutlerRequestError) as err:
        await client.clock("pause")

    assert err.value.status == status
    assert not isinstance(err.value, TimebutlerAuthError)


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"status": 401}, TimebutlerAuthError),
        ({"status": 500}, TimebutlerConnectionError),
        ({"exc": aiohttp.ClientConnectionError()}, TimebutlerConnectionError),
    ],
)
async def test_write_auth_and_connection_errors(client, aioclient_mock, kwargs, error):
    aioclient_mock.post(f"{API_BASE}/time-clock/start", **kwargs)

    with pytest.raises(error):
        await client.clock("start")


async def test_projects_and_categories(client, aioclient_mock):
    aioclient_mock.get(f"{API_BASE}/projects", json=load_fixture_json("projects"))
    aioclient_mock.get(f"{API_BASE}/categories", json=load_fixture_json("categories"))

    assert (await client.get_projects())["defaultProjectId"] == "5"
    assert (await client.get_categories())["categories"][1]["name"] == "Meeting"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_api.py -v`
Expected: FAIL / ERROR with `ImportError: cannot import name 'TimebutlerRequestError'`.

- [ ] **Step 4: Implement**

Replace `custom_components/timebutler/api.py` with:

```python
"""Async client for the Timebutler API v2."""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

from .const import API_BASE, REQUEST_TIMEOUT, USER_AGENT

# Answers to write calls that mean "refused", each with its own user message.
REFUSED_STATUSES = (400, 403, 404, 409, 422)

CLOCK_PATHS = {
    "start": "/time-clock/start",
    "pause": "/time-clock/pause",
    "resume": "/time-clock/resume",
    "cancel": "/time-clock/cancel",
    "business_trip_start": "/time-clock/business-trip-start",
    "business_trip_end": "/time-clock/business-trip-end",
}


class TimebutlerError(Exception):
    """Base error of the Timebutler client."""


class TimebutlerAuthError(TimebutlerError):
    """The token was rejected (HTTP 401, or 403 on a read call)."""


class TimebutlerConnectionError(TimebutlerError):
    """Network problem, timeout, rate limit or an unexpected answer."""


class TimebutlerTimeTrackingDisabled(TimebutlerError):
    """Time tracking is not active for the company (HTTP 400)."""


class TimebutlerRequestError(TimebutlerError):
    """A write call was refused; `status` tells why (400, 403, 404, 409, 422)."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class TimebutlerClient:
    """Access to the data of the token's own user."""

    def __init__(
        self, session: aiohttp.ClientSession, token: str, base_url: str = API_BASE
    ) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        time_tracking: bool = False,
        write: bool = False,
    ) -> Any:
        """Send one request and return the decoded JSON body (None on 204)."""
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                response = await self._session.request(
                    method,
                    f"{self._base_url}{path}",
                    headers=self._headers,
                    params=params,
                    json=json,
                )
                try:
                    status = response.status
                    if status == 401 or (status == 403 and not write):
                        raise TimebutlerAuthError(f"{path}: HTTP {status}")
                    if write and status in REFUSED_STATUSES:
                        text = await response.text()
                        raise TimebutlerRequestError(
                            status, f"{method} {path}: HTTP {status} {text[:300]}"
                        )
                    if status == 400 and time_tracking:
                        raise TimebutlerTimeTrackingDisabled(path)
                    if status >= 400:
                        raise TimebutlerConnectionError(f"{path}: HTTP {status}")
                    if status == 204:
                        return None
                    return await response.json(content_type=None)
                finally:
                    response.release()
        except TimebutlerError:
            raise
        except (TimeoutError, aiohttp.ClientError, ValueError) as err:
            raise TimebutlerConnectionError(f"{path}: {err!r}") from err

    async def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        time_tracking: bool = False,
    ) -> Any:
        """GET a path and return the decoded JSON body."""
        return await self._request("GET", path, params=params, time_tracking=time_tracking)

    async def _write(self, method: str, path: str, json: dict[str, Any] | None = None) -> Any:
        """POST/DELETE a path; refusals raise TimebutlerRequestError."""
        return await self._request(method, path, json=json, write=True)

    async def get_profile(self) -> dict[str, Any]:
        """Profile of the token's user (id, name, ...)."""
        return await self._get("/user/profile")

    async def get_clock_status(self) -> dict[str, Any]:
        """State of the time clock."""
        return await self._get("/time-clock/status", time_tracking=True)

    async def get_work_summary(self) -> dict[str, Any]:
        """Today's target, worked, remaining and break minutes."""
        return await self._get("/time-tracking/work-summary", time_tracking=True)

    async def get_time_off_summary(self) -> dict[str, Any]:
        """Vacation and illness days of the current year."""
        return await self._get("/time-tracking/time-off-summary", time_tracking=True)

    async def get_absence_requests(self) -> list[dict[str, Any]]:
        """All own absence requests, in every state."""
        return await self._get("/absence-requests/my")

    async def get_absence_types(self) -> list[dict[str, Any]]:
        """Absence types; `id` is the 3-letter code used in requests."""
        return await self._get("/absence-types")

    async def get_time_entries(self, year: int, month: int) -> list[dict[str, Any]]:
        """Own time entries of one month (empty when time tracking is off)."""
        return await self._get("/time-entries/my", {"year": year, "month": month})

    async def get_projects(self) -> dict[str, Any]:
        """Active projects plus the user's default project."""
        return await self._get("/projects")

    async def get_categories(self) -> dict[str, Any]:
        """Active categories plus the user's default category."""
        return await self._get("/categories")

    async def clock(self, action: str) -> dict[str, Any]:
        """Run a time clock action (see CLOCK_PATHS) and return the new status."""
        return await self._write("POST", CLOCK_PATHS[action])

    async def clock_stop(self, body: dict[str, Any]) -> dict[str, Any] | None:
        """Stop and save the clock; None when a waiting clock was discarded."""
        return await self._write("POST", "/time-clock/stop", body)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_api.py -v`
Expected: all PASS (old read-path tests unchanged, including `403 → TimebutlerAuthError` for reads).

- [ ] **Step 6: Run the whole suite**

Run: `pytest`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add custom_components/timebutler/api.py tests/test_api.py tests/fixtures/projects.json tests/fixtures/categories.json
git commit -m "feat: add write path and time clock calls to the API client"
```

---

### Task 2: Model helpers for the clock

**Files:**
- Modify: `custom_components/timebutler/model.py`
- Create: `tests/fixtures/stop_saved.json`, `tests/fixtures/stop_confirmation.json`
- Test: `tests/test_model.py`

**Interfaces:**
- Consumes: nothing new.
- Produces (all in `model.py`):
  - `class InputError(Exception)` with `key: str` and `placeholders: dict[str, str]`; constructor `InputError(key, **placeholders)`.
  - `match_item(value: str, items: list[dict[str, Any]], unknown_key: str) -> str` – ID of the item whose `id` (case-insensitive) or else `name` (case-insensitive, trimmed) equals `value`; raises `InputError(unknown_key, name=value, options="A, B")`.
  - `assignment_id(value: str | None, items: list[dict[str, Any]], default_id: str | None, unknown_key: str) -> int` – named item, else `int(default_id)`, else `0`.
  - `stop_result(response: dict[str, Any] | None) -> dict[str, Any]`.
  - `break_regulation_message(info: dict[str, Any], language: str) -> tuple[str, str]` – `(title, markdown message)`.

- [ ] **Step 1: Add fixtures**

`tests/fixtures/stop_saved.json`:
```json
{"outcome": "saved", "id": "98765", "workedMinutes": 180, "breakMinutes": 0, "breakRegulationApplied": false, "breakRegulationIgnored": false, "breakRegulation": null}
```

`tests/fixtures/stop_confirmation.json`:
```json
{"outcome": "confirmationRequired", "id": null, "workedMinutes": null, "breakMinutes": null, "breakRegulationApplied": false, "breakRegulationIgnored": false, "breakRegulation": {"hasNoRemedy": false, "obligatory": false, "correctionWorkMinutes": 30, "correctionBreakMinutes": 30, "resultingWorkMinutes": 450, "resultingBreakMinutes": 30, "detailText": "Required change: working time -0:30, break +0:30"}}
```

- [ ] **Step 2: Write the failing tests**

In `tests/test_model.py` extend the model import to:

```python
from custom_components.timebutler.model import (
    ClockState,
    InputError,
    TodaySummary,
    assignment_id,
    break_regulation_message,
    match_item,
    month_minutes,
    months_needed,
    parse_clock,
    stop_result,
    summary_includes_running,
    today_summary,
    upcoming_absences,
    week_minutes,
)
```

Append:

```python
PROJECTS = load_fixture_json("projects")["projects"]
BREAK_INFO = load_fixture_json("stop_confirmation")["breakRegulation"]


def test_match_item_by_id_or_name():
    assert match_item("6", PROJECTS, "unknown_project") == "6"
    assert match_item("  kundenprojekt a ", PROJECTS, "unknown_project") == "5"


def test_match_item_prefers_ids_over_names():
    items = [{"id": "1", "name": "2"}, {"id": "2", "name": "Zwei"}]

    assert match_item("2", items, "unknown_project") == "2"


def test_match_item_unknown_lists_names():
    with pytest.raises(InputError) as err:
        match_item("Extern", PROJECTS, "unknown_project")

    assert err.value.key == "unknown_project"
    assert err.value.placeholders == {"name": "Extern", "options": "Kundenprojekt A, Intern"}


def test_match_item_unknown_without_items():
    with pytest.raises(InputError) as err:
        match_item("Extern", [], "unknown_category")

    assert err.value.placeholders["options"] == "-"


def test_assignment_id_uses_value_then_default_then_zero():
    assert assignment_id("Intern", PROJECTS, "5", "unknown_project") == 6
    assert assignment_id(None, PROJECTS, "5", "unknown_project") == 5
    assert assignment_id("", [], None, "unknown_project") == 0


def test_stop_result():
    assert stop_result(load_fixture_json("stop_saved")) == {
        "outcome": "saved",
        "id": "98765",
        "gearbeitet": 180,
        "pause": 0,
    }
    assert stop_result(None) == {"outcome": "discarded"}


def test_break_regulation_message_offers_apply_and_ignore():
    title, message = break_regulation_message(BREAK_INFO, "de")

    assert title == "Timebutler: Pausenregelung"
    assert message.startswith("Required change: working time -0:30, break +0:30")
    assert "break_regulation: apply" in message
    assert "break_regulation: ignore" in message
    assert "timebutler.cancel" not in message


def test_break_regulation_message_obligatory_hides_ignore():
    _, message = break_regulation_message({**BREAK_INFO, "obligatory": True}, "en")

    assert "break_regulation: apply" in message
    assert "ignore" not in message


def test_break_regulation_message_without_remedy_offers_discard():
    info = {**BREAK_INFO, "hasNoRemedy": True, "obligatory": True}

    title, message = break_regulation_message(info, "en-GB")

    assert title == "Timebutler: break regulation"
    assert "break_regulation: apply" not in message
    assert "timebutler.cancel" in message
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_model.py -v`
Expected: ERROR `ImportError: cannot import name 'InputError'`.

- [ ] **Step 4: Implement**

Append to `custom_components/timebutler/model.py`:

```python
class InputError(Exception):
    """Invalid action input; `key` is the translation key of the message."""

    def __init__(self, key: str, **placeholders: str) -> None:
        super().__init__(key)
        self.key = key
        self.placeholders = placeholders


def match_item(value: str, items: list[dict[str, Any]], unknown_key: str) -> str:
    """ID of the item whose ID, else name, equals value (case-insensitive)."""
    wanted = value.strip().casefold()
    for field in ("id", "name"):
        for item in items:
            if str(item.get(field) or "").strip().casefold() == wanted:
                return str(item.get("id"))
    names = ", ".join(str(item.get("name")) for item in items)
    raise InputError(unknown_key, name=value, options=names or "-")


def assignment_id(
    value: str | None, items: list[dict[str, Any]], default_id: str | None, unknown_key: str
) -> int:
    """projectId/categoryId to send: the named one, else the default, else 0 (none)."""
    if value:
        return int(match_item(value, items, unknown_key))
    return int(default_id) if default_id else 0


def stop_result(response: dict[str, Any] | None) -> dict[str, Any]:
    """Service response of a stop; None means a waiting clock was discarded."""
    if response is None:
        return {"outcome": "discarded"}
    return {
        "outcome": "saved",
        "id": response.get("id"),
        "gearbeitet": response.get("workedMinutes"),
        "pause": response.get("breakMinutes"),
    }


_BREAK_TEXT = {
    "de": {
        "title": "Timebutler: Pausenregelung",
        "intro": "{detail}\n\nNichts wurde gespeichert, die Uhr läuft weiter.",
        "apply": "Korrektur übernehmen: **Stopp mit Pausenkorrektur** drücken oder `timebutler.stop` mit `break_regulation: apply` aufrufen.",
        "ignore": "Unverändert speichern: `timebutler.stop` mit `break_regulation: ignore` aufrufen.",
        "no_remedy": "Eine automatische Korrektur ist nicht möglich.",
        "discard": "Sitzung verwerfen: **Verwerfen** drücken oder `timebutler.cancel` aufrufen.",
    },
    "en": {
        "title": "Timebutler: break regulation",
        "intro": "{detail}\n\nNothing was saved, the clock keeps running.",
        "apply": "Accept the correction: press **Stop with break correction** or call `timebutler.stop` with `break_regulation: apply`.",
        "ignore": "Save unchanged: call `timebutler.stop` with `break_regulation: ignore`.",
        "no_remedy": "The violation cannot be corrected automatically.",
        "discard": "Discard the session: press **Discard** or call `timebutler.cancel`.",
    },
}


def break_regulation_message(info: dict[str, Any], language: str) -> tuple[str, str]:
    """Title and text of the notification asking for a break-regulation decision."""
    text = _BREAK_TEXT["de" if language.lower().startswith("de") else "en"]
    parts = [text["intro"].format(detail=info.get("detailText") or "")]
    parts.append(text["no_remedy"] if info.get("hasNoRemedy") else text["apply"])
    if not info.get("obligatory"):
        parts.append(text["ignore"])
    if info.get("hasNoRemedy"):
        parts.append(text["discard"])
    return text["title"], "\n\n".join(parts)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_model.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add custom_components/timebutler/model.py tests/test_model.py tests/fixtures/stop_saved.json tests/fixtures/stop_confirmation.json
git commit -m "feat: add input resolution and break regulation text to the model"
```

---

### Task 3: Clock actions and services

**Files:**
- Create: `custom_components/timebutler/actions.py`, `custom_components/timebutler/services.py`, `custom_components/timebutler/services.yaml`, `tests/fixtures/clock_paused.json`, `tests/test_services.py`
- Modify: `custom_components/timebutler/const.py`, `custom_components/timebutler/__init__.py`, `custom_components/timebutler/strings.json`, `custom_components/timebutler/translations/en.json`, `custom_components/timebutler/translations/de.json`, `tests/common.py`

**Interfaces:**
- Consumes: Task 1 `TimebutlerClient.clock`, `clock_stop`, `get_projects`, `get_categories`, `TimebutlerRequestError.status`; Task 2 `InputError`, `assignment_id`, `stop_result`, `break_regulation_message`; existing `parse_clock`, `LiveData`, `TimebutlerConfigEntry` (`entry.runtime_data.live` / `.background`, `live.client`).
- Produces:
  - `const.EVENT_BREAK_REGULATION = "timebutler_break_regulation"`.
  - `actions.async_clock_action(hass, entry, action: str) -> None` (`action` as in `CLOCK_PATHS`).
  - `actions.async_stop(hass, entry, *, project: str | None = None, category: str | None = None, remarks: str | None = None, break_regulation: str | None = None) -> dict[str, Any]`.
  - `actions.checked_input()` context manager: `InputError` → `ServiceValidationError`.
  - `actions.call_api(hass, entry, request: Awaitable[T], errors: dict[int, str]) -> T`.
  - `actions.async_assignments(hass, entry, project, category) -> dict[str, int]` (keys `projectId` / `categoryId`, each only when known).
  - `services.ATTR_CONFIG_ENTRY_ID`, `services.BASE_SCHEMA`, `services.resolve_entry(hass, call)`, `services.async_setup_services(hass)`.
  - Tests helpers in `tests/common.py`: `setup_integration(hass, aioclient_mock, overrides=None) -> MockConfigEntry`, `remock(aioclient_mock, overrides=None)`, `api_calls(aioclient_mock, method: str, path: str) -> list`.

- [ ] **Step 1: Add fixture and test helpers**

`tests/fixtures/clock_paused.json`:
```json
{"status": "paused", "startTimestamp": 1791381600000, "pauseTimestamp": 1791392400000, "workTimeElapsedSeconds": 10800, "breakElapsedSeconds": 0, "accumulatedBreakSeconds": 0, "waitSeconds": null, "isBusinessTripActive": false, "startOnlyOnTerminal": false, "pauseOnlyOnTerminal": false, "stopOnlyOnTerminal": false}
```

Replace `tests/common.py` with:

```python
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
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_services.py`:

```python
"""Services: account selection, clock actions, stop and break regulation."""
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.timebutler.const import API_BASE, DOMAIN
from custom_components.timebutler.coordinator import TimebutlerLiveCoordinator
from tests.common import api_calls, load_fixture_json, remock, setup_integration

pytestmark = pytest.mark.usefixtures("auto_enable_custom_integrations")


async def _call(hass, service, data=None, response=False):
    return await hass.services.async_call(
        DOMAIN, service, data or {}, blocking=True, return_response=response
    )


def _mock_assignments(aioclient_mock, projects=None, categories=None):
    aioclient_mock.get(f"{API_BASE}/projects", **(projects or {"json": load_fixture_json("projects")}))
    aioclient_mock.get(f"{API_BASE}/categories", **(categories or {"json": load_fixture_json("categories")}))


def _reauth_started(hass) -> bool:
    return any(flow["context"]["source"] == SOURCE_REAUTH for flow in hass.config_entries.flow.async_progress())


async def test_pause_posts_and_shows_new_state(hass, aioclient_mock, frozen_now):
    await setup_integration(hass, aioclient_mock)
    remock(aioclient_mock, {"/time-clock/status": "clock_paused"})
    aioclient_mock.post(f"{API_BASE}/time-clock/pause", json=load_fixture_json("clock_paused"))

    await _call(hass, "pause")

    assert api_calls(aioclient_mock, "POST", "/time-clock/pause") == [None]
    assert hass.states.get("sensor.timebutler_stempeluhr").state == "paused"


async def test_clock_state_is_shown_before_the_next_poll(hass, aioclient_mock, frozen_now):
    await setup_integration(hass, aioclient_mock)
    aioclient_mock.post(f"{API_BASE}/time-clock/pause", json=load_fixture_json("clock_paused"))

    with patch.object(TimebutlerLiveCoordinator, "async_request_refresh", AsyncMock()):
        await _call(hass, "pause")

    assert hass.states.get("sensor.timebutler_stempeluhr").state == "paused"
    assert hass.states.get("binary_sensor.timebutler_eingestempelt").state == "on"


@pytest.mark.parametrize(
    ("service", "path", "status", "key"),
    [
        ("start", "/time-clock/start", 409, "clock_start_conflict"),
        ("pause", "/time-clock/pause", 409, "clock_not_running"),
        ("resume", "/time-clock/resume", 409, "clock_not_paused"),
        ("cancel", "/time-clock/cancel", 409, "clock_already_idle"),
        ("start", "/time-clock/start", 400, "clock_disabled"),
        ("business_trip_start", "/time-clock/business-trip-start", 400, "business_trip_disabled"),
        ("business_trip_end", "/time-clock/business-trip-end", 409, "business_trip_conflict"),
        ("pause", "/time-clock/pause", 404, "request_rejected"),
    ],
)
async def test_refused_clock_actions_are_translated(hass, aioclient_mock, frozen_now, service, path, status, key):
    await setup_integration(hass, aioclient_mock)
    aioclient_mock.post(f"{API_BASE}{path}", status=status)

    with pytest.raises(HomeAssistantError) as err:
        await _call(hass, service)

    assert err.value.translation_key == key


async def test_terminal_only_does_not_start_reauth(hass, aioclient_mock, frozen_now):
    await setup_integration(hass, aioclient_mock)
    aioclient_mock.post(f"{API_BASE}/time-clock/pause", status=403)

    with pytest.raises(HomeAssistantError) as err:
        await _call(hass, "pause")
    await hass.async_block_till_done()

    assert err.value.translation_key == "terminal_only"
    assert not _reauth_started(hass)


async def test_rejected_token_starts_reauth(hass, aioclient_mock, frozen_now):
    await setup_integration(hass, aioclient_mock)
    aioclient_mock.post(f"{API_BASE}/time-clock/pause", status=401)

    with pytest.raises(HomeAssistantError) as err:
        await _call(hass, "pause")
    await hass.async_block_till_done()

    assert err.value.translation_key == "auth_failed"
    assert _reauth_started(hass)


async def test_unreachable_timebutler(hass, aioclient_mock, frozen_now):
    await setup_integration(hass, aioclient_mock)
    aioclient_mock.post(f"{API_BASE}/time-clock/pause", exc=aiohttp.ClientConnectionError())

    with pytest.raises(HomeAssistantError) as err:
        await _call(hass, "pause")

    assert err.value.translation_key == "connection_error"


async def test_stop_sends_company_defaults_and_returns_result(hass, aioclient_mock, frozen_now):
    await setup_integration(hass, aioclient_mock)
    remock(aioclient_mock, {"/time-clock/status": "clock_idle"})
    _mock_assignments(aioclient_mock)
    aioclient_mock.post(f"{API_BASE}/time-clock/stop", json=load_fixture_json("stop_saved"))

    result = await _call(hass, "stop", response=True)

    assert result == {"outcome": "saved", "id": "98765", "gearbeitet": 180, "pause": 0}
    assert api_calls(aioclient_mock, "POST", "/time-clock/stop") == [{"projectId": 5, "categoryId": 0}]
    assert hass.states.get("sensor.timebutler_stempeluhr").state == "idle"
    # The finished session is a time entry now: week and month are fetched again.
    assert api_calls(aioclient_mock, "GET", "/time-entries/my")


async def test_stop_resolves_project_category_and_options(hass, aioclient_mock, frozen_now):
    await setup_integration(hass, aioclient_mock)
    _mock_assignments(aioclient_mock)
    aioclient_mock.post(f"{API_BASE}/time-clock/stop", json=load_fixture_json("stop_saved"))

    await _call(hass, "stop", {"project": "intern", "category": "Meeting", "remarks": "Review", "break_regulation": "ignore"})

    assert api_calls(aioclient_mock, "POST", "/time-clock/stop") == [
        {"projectId": 6, "categoryId": 8, "remarks": "Review", "breakRegulation": "ignore"}
    ]


async def test_stop_with_unknown_project(hass, aioclient_mock, frozen_now):
    await setup_integration(hass, aioclient_mock)
    _mock_assignments(aioclient_mock)
    aioclient_mock.post(f"{API_BASE}/time-clock/stop", json=load_fixture_json("stop_saved"))

    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "stop", {"project": "Extern"})

    assert err.value.translation_key == "unknown_project"
    assert err.value.translation_placeholders == {"name": "Extern", "options": "Kundenprojekt A, Intern"}
    assert api_calls(aioclient_mock, "POST", "/time-clock/stop") == []


async def test_stop_still_works_without_project_list(hass, aioclient_mock, frozen_now):
    await setup_integration(hass, aioclient_mock)
    _mock_assignments(aioclient_mock, {"status": 500}, {"exc": aiohttp.ClientConnectionError()})
    aioclient_mock.post(f"{API_BASE}/time-clock/stop", json=load_fixture_json("stop_saved"))

    await _call(hass, "stop")

    assert api_calls(aioclient_mock, "POST", "/time-clock/stop") == [{}]


async def test_stop_of_waiting_clock(hass, aioclient_mock, frozen_now):
    await setup_integration(hass, aioclient_mock)
    _mock_assignments(aioclient_mock)
    aioclient_mock.post(f"{API_BASE}/time-clock/stop", status=204)

    assert await _call(hass, "stop", response=True) == {"outcome": "discarded"}


async def test_break_regulation_needs_confirmation(hass, aioclient_mock, frozen_now):
    entry = await setup_integration(hass, aioclient_mock)
    _mock_assignments(aioclient_mock)
    aioclient_mock.post(f"{API_BASE}/time-clock/stop", json=load_fixture_json("stop_confirmation"))
    events = async_capture_events(hass, "timebutler_break_regulation")

    with patch("custom_components.timebutler.actions.persistent_notification") as notify:
        with pytest.raises(HomeAssistantError) as err:
            await _call(hass, "stop")
    await hass.async_block_till_done()

    assert err.value.translation_key == "break_regulation_confirmation"
    assert err.value.translation_placeholders == {"detail": "Required change: working time -0:30, break +0:30"}
    assert [event.data for event in events] == [
        {
            "config_entry_id": entry.entry_id,
            "detail": "Required change: working time -0:30, break +0:30",
            "obligatory": False,
            "has_no_remedy": False,
            "correction_work_minutes": 30,
            "correction_break_minutes": 30,
            "resulting_work_minutes": 450,
            "resulting_break_minutes": 30,
        }
    ]
    assert notify.async_create.call_args.kwargs["notification_id"] == f"timebutler_break_regulation_{entry.entry_id}"
    assert "break_regulation: apply" in notify.async_create.call_args.args[1]
    notify.async_dismiss.assert_not_called()
    assert hass.states.get("sensor.timebutler_stempeluhr").state == "running"


async def test_successful_stop_dismisses_break_regulation_notice(hass, aioclient_mock, frozen_now):
    entry = await setup_integration(hass, aioclient_mock)
    _mock_assignments(aioclient_mock)
    aioclient_mock.post(f"{API_BASE}/time-clock/stop", json=load_fixture_json("stop_saved"))

    with patch("custom_components.timebutler.actions.persistent_notification") as notify:
        await _call(hass, "stop", {"break_regulation": "apply"})

    notify.async_dismiss.assert_called_once_with(hass, f"timebutler_break_regulation_{entry.entry_id}")
    assert api_calls(aioclient_mock, "POST", "/time-clock/stop")[0]["breakRegulation"] == "apply"


async def test_account_selection(hass, aioclient_mock, frozen_now):
    entry = await setup_integration(hass, aioclient_mock)
    aioclient_mock.post(f"{API_BASE}/time-clock/pause", json=load_fixture_json("clock_paused"))

    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "pause", {"config_entry_id": "nope"})
    assert err.value.translation_key == "entry_not_found"

    await _call(hass, "pause", {"config_entry_id": entry.entry_id})
    assert len(api_calls(aioclient_mock, "POST", "/time-clock/pause")) == 1

    assert await hass.config_entries.async_unload(entry.entry_id)
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "pause", {"config_entry_id": entry.entry_id})
    assert err.value.translation_key == "entry_not_loaded"
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "pause")
    assert err.value.translation_key == "entry_required"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_services.py -v`
Expected: FAIL with `ServiceNotFound` (service `timebutler.pause` not registered).

- [ ] **Step 4: Add the event constant**

Append to `custom_components/timebutler/const.py`:

```python
EVENT_BREAK_REGULATION = f"{DOMAIN}_break_regulation"
```

- [ ] **Step 5: Create `custom_components/timebutler/actions.py`**

```python
"""Actions on Timebutler, shared by buttons and services."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
import logging
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .api import (
    TimebutlerAuthError,
    TimebutlerError,
    TimebutlerRequestError,
)
from .const import DOMAIN, EVENT_BREAK_REGULATION
from .coordinator import LiveData, TimebutlerConfigEntry
from .model import (
    InputError,
    assignment_id,
    break_regulation_message,
    parse_clock,
    stop_result,
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
```

- [ ] **Step 6: Create `custom_components/timebutler/services.py`**

```python
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

from .actions import async_clock_action, async_stop
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
```

- [ ] **Step 7: Register services in `custom_components/timebutler/__init__.py`**

Replace the imports and add `CONFIG_SCHEMA` and `async_setup` so the top of the file reads:

```python
"""The Timebutler integration."""

from __future__ import annotations

from homeassistant.const import CONF_API_TOKEN, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import TimebutlerClient
from .const import DOMAIN
from .coordinator import (
    TimebutlerBackgroundCoordinator,
    TimebutlerConfigEntry,
    TimebutlerLiveCoordinator,
    TimebutlerRuntime,
)
from .services import async_setup_services

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the services once, for all entries."""
    async_setup_services(hass)
    return True
```

(`async_setup_entry` / `async_unload_entry` stay unchanged.)

- [ ] **Step 8: Create `custom_components/timebutler/services.yaml`**

```yaml
start: &clock_action
  fields:
    config_entry_id: &config_entry_id
      selector:
        config_entry:
          integration: timebutler
pause: *clock_action
resume: *clock_action
cancel: *clock_action
business_trip_start: *clock_action
business_trip_end: *clock_action
stop:
  fields:
    config_entry_id: *config_entry_id
    project:
      example: Kundenprojekt A
      selector:
        text:
    category:
      example: Entwicklung
      selector:
        text:
    remarks:
      selector:
        text:
          multiline: true
    break_regulation:
      selector:
        select:
          options:
            - apply
            - ignore
          translation_key: break_regulation
```

- [ ] **Step 9: Add translations**

In `custom_components/timebutler/strings.json` **and** `custom_components/timebutler/translations/en.json` add these top-level keys (after `"entity"`; keep both files identical):

```json
  "services": {
    "start": {
      "name": "Start clock",
      "description": "Starts the Timebutler time clock.",
      "fields": {
        "config_entry_id": {"name": "Account", "description": "Timebutler account; only needed when several are set up."}
      }
    },
    "pause": {
      "name": "Pause clock",
      "description": "Pauses the running time clock.",
      "fields": {
        "config_entry_id": {"name": "Account", "description": "Timebutler account; only needed when several are set up."}
      }
    },
    "resume": {
      "name": "Resume clock",
      "description": "Resumes the paused time clock.",
      "fields": {
        "config_entry_id": {"name": "Account", "description": "Timebutler account; only needed when several are set up."}
      }
    },
    "cancel": {
      "name": "Discard session",
      "description": "Discards the current clock session without saving a time entry.",
      "fields": {
        "config_entry_id": {"name": "Account", "description": "Timebutler account; only needed when several are set up."}
      }
    },
    "business_trip_start": {
      "name": "Start business trip",
      "description": "Marks the start of a business trip on the active clock.",
      "fields": {
        "config_entry_id": {"name": "Account", "description": "Timebutler account; only needed when several are set up."}
      }
    },
    "business_trip_end": {
      "name": "End business trip",
      "description": "Ends the business trip on the active clock.",
      "fields": {
        "config_entry_id": {"name": "Account", "description": "Timebutler account; only needed when several are set up."}
      }
    },
    "stop": {
      "name": "Stop clock",
      "description": "Stops the clock and saves the time entry.",
      "fields": {
        "config_entry_id": {"name": "Account", "description": "Timebutler account; only needed when several are set up."},
        "project": {"name": "Project", "description": "Project name or ID; defaults to your default project."},
        "category": {"name": "Category", "description": "Category name or ID; defaults to your default category."},
        "remarks": {"name": "Remarks", "description": "Note for the time entry."},
        "break_regulation": {"name": "Break regulation", "description": "Decision when the session violates the break regulation."}
      }
    }
  },
  "selector": {
    "break_regulation": {
      "options": {
        "apply": "Apply the correction",
        "ignore": "Save unchanged"
      }
    }
  },
  "exceptions": {
    "auth_failed": {"message": "Timebutler rejected the token. Re-authenticate the integration."},
    "connection_error": {"message": "Timebutler could not be reached."},
    "request_rejected": {"message": "Timebutler refused the request (HTTP {status})."},
    "entry_required": {"message": "Select the Timebutler account (config_entry_id): none or several are loaded."},
    "entry_not_found": {"message": "Unknown Timebutler account."},
    "entry_not_loaded": {"message": "This Timebutler account is not loaded."},
    "clock_disabled": {"message": "Time tracking or the time clock is not active for your company."},
    "business_trip_disabled": {"message": "Business trips are not enabled for your company."},
    "terminal_only": {"message": "You may use the time clock only at the time recording terminal."},
    "clock_start_conflict": {"message": "The clock is already running or cannot be started right now."},
    "clock_not_running": {"message": "The clock is not running."},
    "clock_not_paused": {"message": "The clock is not paused."},
    "clock_already_idle": {"message": "The clock is already off."},
    "business_trip_conflict": {"message": "The clock is not active, or the business trip is already running or not running."},
    "clock_stop_conflict": {"message": "Cannot stop: clock not active, session shorter than one minute, or duplicate entry."},
    "invalid_assignment": {"message": "Timebutler rejected the project or category."},
    "unknown_project": {"message": "Unknown project \"{name}\". Available: {options}"},
    "unknown_category": {"message": "Unknown category \"{name}\". Available: {options}"},
    "break_regulation_confirmation": {"message": "Break regulation: {detail}. Nothing was saved, the clock keeps running – see the notification."}
  }
```

In `custom_components/timebutler/translations/de.json` add:

```json
  "services": {
    "start": {
      "name": "Stempeluhr starten",
      "description": "Startet die Timebutler-Stempeluhr.",
      "fields": {
        "config_entry_id": {"name": "Konto", "description": "Timebutler-Konto; nur nötig, wenn mehrere eingerichtet sind."}
      }
    },
    "pause": {
      "name": "Stempeluhr pausieren",
      "description": "Pausiert die laufende Stempeluhr.",
      "fields": {
        "config_entry_id": {"name": "Konto", "description": "Timebutler-Konto; nur nötig, wenn mehrere eingerichtet sind."}
      }
    },
    "resume": {
      "name": "Stempeluhr fortsetzen",
      "description": "Setzt die pausierte Stempeluhr fort.",
      "fields": {
        "config_entry_id": {"name": "Konto", "description": "Timebutler-Konto; nur nötig, wenn mehrere eingerichtet sind."}
      }
    },
    "cancel": {
      "name": "Sitzung verwerfen",
      "description": "Verwirft die laufende Sitzung, ohne einen Zeiteintrag zu speichern.",
      "fields": {
        "config_entry_id": {"name": "Konto", "description": "Timebutler-Konto; nur nötig, wenn mehrere eingerichtet sind."}
      }
    },
    "business_trip_start": {
      "name": "Dienstreise starten",
      "description": "Markiert den Beginn einer Dienstreise auf der aktiven Uhr.",
      "fields": {
        "config_entry_id": {"name": "Konto", "description": "Timebutler-Konto; nur nötig, wenn mehrere eingerichtet sind."}
      }
    },
    "business_trip_end": {
      "name": "Dienstreise beenden",
      "description": "Beendet die Dienstreise auf der aktiven Uhr.",
      "fields": {
        "config_entry_id": {"name": "Konto", "description": "Timebutler-Konto; nur nötig, wenn mehrere eingerichtet sind."}
      }
    },
    "stop": {
      "name": "Stempeluhr stoppen",
      "description": "Stoppt die Uhr und speichert den Zeiteintrag.",
      "fields": {
        "config_entry_id": {"name": "Konto", "description": "Timebutler-Konto; nur nötig, wenn mehrere eingerichtet sind."},
        "project": {"name": "Projekt", "description": "Projektname oder ID; ohne Angabe dein Standardprojekt."},
        "category": {"name": "Kategorie", "description": "Kategoriename oder ID; ohne Angabe deine Standardkategorie."},
        "remarks": {"name": "Bemerkung", "description": "Notiz zum Zeiteintrag."},
        "break_regulation": {"name": "Pausenregelung", "description": "Entscheidung, wenn die Sitzung die Pausenregelung verletzt."}
      }
    }
  },
  "selector": {
    "break_regulation": {
      "options": {
        "apply": "Korrektur übernehmen",
        "ignore": "Unverändert speichern"
      }
    }
  },
  "exceptions": {
    "auth_failed": {"message": "Timebutler hat den Token abgelehnt. Bitte die Integration neu anmelden."},
    "connection_error": {"message": "Timebutler ist nicht erreichbar."},
    "request_rejected": {"message": "Timebutler hat die Anfrage abgelehnt (HTTP {status})."},
    "entry_required": {"message": "Bitte das Timebutler-Konto angeben (config_entry_id): keins oder mehrere sind geladen."},
    "entry_not_found": {"message": "Unbekanntes Timebutler-Konto."},
    "entry_not_loaded": {"message": "Dieses Timebutler-Konto ist nicht geladen."},
    "clock_disabled": {"message": "Zeiterfassung oder Stempeluhr ist für deine Firma nicht aktiv."},
    "business_trip_disabled": {"message": "Dienstreisen sind für deine Firma nicht aktiviert."},
    "terminal_only": {"message": "Stempeln ist für dich nur am Terminal erlaubt."},
    "clock_start_conflict": {"message": "Die Uhr läuft bereits oder kann gerade nicht gestartet werden."},
    "clock_not_running": {"message": "Die Uhr läuft nicht."},
    "clock_not_paused": {"message": "Die Uhr ist nicht pausiert."},
    "clock_already_idle": {"message": "Die Uhr ist bereits aus."},
    "business_trip_conflict": {"message": "Die Uhr ist nicht aktiv oder die Dienstreise läuft bereits bzw. nicht."},
    "clock_stop_conflict": {"message": "Stoppen nicht möglich: Uhr nicht aktiv, Sitzung kürzer als eine Minute oder doppelter Eintrag."},
    "invalid_assignment": {"message": "Timebutler hat Projekt oder Kategorie abgelehnt."},
    "unknown_project": {"message": "Unbekanntes Projekt „{name}“. Verfügbar: {options}"},
    "unknown_category": {"message": "Unbekannte Kategorie „{name}“. Verfügbar: {options}"},
    "break_regulation_confirmation": {"message": "Pausenregelung: {detail}. Nichts gespeichert, die Uhr läuft weiter – siehe Benachrichtigung."}
  }
```

Validate JSON afterwards:

```bash
python -c "import json; [json.load(open(f'custom_components/timebutler/{p}', encoding='utf-8')) for p in ('strings.json', 'translations/en.json', 'translations/de.json')]; print('ok')"
```
Expected: `ok`. Also `diff custom_components/timebutler/strings.json custom_components/timebutler/translations/en.json` prints nothing.

- [ ] **Step 10: Run tests to verify they pass**

Run: `pytest tests/test_services.py -v`
Expected: all PASS.

- [ ] **Step 11: Run the whole suite**

Run: `pytest`
Expected: all PASS.

- [ ] **Step 12: Commit**

```bash
git add custom_components/timebutler tests/common.py tests/test_services.py tests/fixtures/clock_paused.json
git commit -m "feat: add time clock services with break regulation handling"
```

---

### Task 4: Time clock buttons

**Files:**
- Create: `custom_components/timebutler/button.py`, `tests/test_button.py`
- Modify: `custom_components/timebutler/__init__.py` (PLATFORMS), `strings.json`, `translations/en.json`, `translations/de.json`

**Interfaces:**
- Consumes: Task 3 `actions.async_clock_action`, `actions.async_stop`; existing `TimebutlerEntity(entry, runtime, key, sources)`, `ClockState` (`status`, `business_trip`, `clocked_in`).
- Produces: `button.BUTTONS: tuple[TimebutlerButtonDescription, ...]`, each with `key`, `available_fn: Callable[[ClockState], bool]`, `press_fn: Callable[[HomeAssistant, TimebutlerConfigEntry], Awaitable[Any]]`, `entity_registry_enabled_default`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_button.py`:

```python
"""Time clock buttons: availability per clock state and pressing."""
import pytest
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.helpers import entity_registry as er

from custom_components.timebutler.button import BUTTONS
from custom_components.timebutler.const import API_BASE
from custom_components.timebutler.model import parse_clock
from tests.common import api_calls, load_fixture_json, remock, setup_integration

pytestmark = pytest.mark.usefixtures("auto_enable_custom_integrations")

MAIN = ("start", "pause", "fortsetzen", "stopp", "stopp_pausenkorrektur")


def _available(hass) -> set[str]:
    return {
        key for key in MAIN if hass.states.get(f"button.timebutler_{key}").state != STATE_UNAVAILABLE
    }


async def _press(hass, key):
    await hass.services.async_call(
        "button", "press", {"entity_id": f"button.timebutler_{key}"}, blocking=True
    )


@pytest.mark.parametrize(
    ("dto", "expected"),
    [
        ({"status": "idle"}, {"start"}),
        ({"status": "running"}, {"pause", "stopp", "stopp_pausenkorrektur", "verwerfen", "dienstreise_start"}),
        (
            {"status": "paused", "isBusinessTripActive": True},
            {"fortsetzen", "stopp", "stopp_pausenkorrektur", "verwerfen", "dienstreise_ende"},
        ),
        ({"status": "waiting"}, {"stopp", "verwerfen"}),
        ({"status": "bogus"}, set()),
    ],
)
def test_availability_rules(dto, expected):
    clock = parse_clock(dto)

    assert {description.key for description in BUTTONS if description.available_fn(clock)} == expected


async def test_buttons_follow_the_running_clock(hass, aioclient_mock, frozen_now):
    await setup_integration(hass, aioclient_mock)

    assert _available(hass) == {"pause", "stopp", "stopp_pausenkorrektur"}
    assert hass.states.get("button.timebutler_stopp").attributes["friendly_name"] == "Timebutler Stop"


async def test_buttons_unavailable_without_time_tracking(hass, aioclient_mock, frozen_now):
    await setup_integration(
        hass,
        aioclient_mock,
        {
            "/time-clock/status": {"status": 400},
            "/time-tracking/work-summary": {"status": 400},
            "/time-entries/my": {"json": []},
        },
    )

    assert _available(hass) == set()


async def test_rare_buttons_are_disabled_by_default(hass, aioclient_mock, frozen_now):
    await setup_integration(hass, aioclient_mock)
    registry = er.async_get(hass)

    for key in ("verwerfen", "dienstreise_start", "dienstreise_ende"):
        entity = registry.async_get(f"button.timebutler_{key}")
        assert entity is not None, key
        assert entity.disabled_by is er.RegistryEntryDisabler.INTEGRATION
        assert hass.states.get(f"button.timebutler_{key}") is None


async def test_press_pause(hass, aioclient_mock, frozen_now):
    await setup_integration(hass, aioclient_mock)
    remock(aioclient_mock, {"/time-clock/status": "clock_paused"})
    aioclient_mock.post(f"{API_BASE}/time-clock/pause", json=load_fixture_json("clock_paused"))

    await _press(hass, "pause")

    assert api_calls(aioclient_mock, "POST", "/time-clock/pause") == [None]
    assert hass.states.get("sensor.timebutler_stempeluhr").state == "paused"
    assert _available(hass) == {"fortsetzen", "stopp", "stopp_pausenkorrektur"}


async def test_press_stop_with_break_correction(hass, aioclient_mock, frozen_now):
    await setup_integration(hass, aioclient_mock)
    remock(aioclient_mock, {"/time-clock/status": "clock_idle"})
    aioclient_mock.get(f"{API_BASE}/projects", json=load_fixture_json("projects"))
    aioclient_mock.get(f"{API_BASE}/categories", json=load_fixture_json("categories"))
    aioclient_mock.post(f"{API_BASE}/time-clock/stop", json=load_fixture_json("stop_saved"))

    await _press(hass, "stopp_pausenkorrektur")

    assert api_calls(aioclient_mock, "POST", "/time-clock/stop") == [
        {"projectId": 5, "categoryId": 0, "breakRegulation": "apply"}
    ]
    assert _available(hass) == {"start"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_button.py -v`
Expected: ERROR `ModuleNotFoundError: No module named 'custom_components.timebutler.button'`.

- [ ] **Step 3: Create `custom_components/timebutler/button.py`**

```python
"""Time clock buttons for the Timebutler integration."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .actions import async_clock_action, async_stop
from .coordinator import TimebutlerConfigEntry, TimebutlerRuntime
from .entity import TimebutlerEntity
from .model import ClockState

PressFn = Callable[[HomeAssistant, TimebutlerConfigEntry], Awaitable[Any]]

STOPPABLE = ("running", "paused", "waiting")


def _clock(action: str) -> PressFn:
    def press(hass: HomeAssistant, entry: TimebutlerConfigEntry) -> Awaitable[None]:
        return async_clock_action(hass, entry, action)

    return press


@dataclass(frozen=True, kw_only=True)
class TimebutlerButtonDescription(ButtonEntityDescription):
    """Button with the clock states it is usable in and its action."""

    available_fn: Callable[[ClockState], bool]
    press_fn: PressFn


BUTTONS: tuple[TimebutlerButtonDescription, ...] = (
    TimebutlerButtonDescription(
        key="start",
        available_fn=lambda clock: clock.status == "idle",
        press_fn=_clock("start"),
    ),
    TimebutlerButtonDescription(
        key="pause",
        available_fn=lambda clock: clock.status == "running",
        press_fn=_clock("pause"),
    ),
    TimebutlerButtonDescription(
        key="fortsetzen",
        available_fn=lambda clock: clock.status == "paused",
        press_fn=_clock("resume"),
    ),
    TimebutlerButtonDescription(
        key="stopp",
        available_fn=lambda clock: clock.status in STOPPABLE,
        press_fn=lambda hass, entry: async_stop(hass, entry),
    ),
    TimebutlerButtonDescription(
        key="stopp_pausenkorrektur",
        available_fn=lambda clock: clock.clocked_in,
        press_fn=lambda hass, entry: async_stop(hass, entry, break_regulation="apply"),
    ),
    TimebutlerButtonDescription(
        key="verwerfen",
        entity_registry_enabled_default=False,
        available_fn=lambda clock: clock.status in STOPPABLE,
        press_fn=_clock("cancel"),
    ),
    TimebutlerButtonDescription(
        key="dienstreise_start",
        entity_registry_enabled_default=False,
        available_fn=lambda clock: clock.clocked_in and not clock.business_trip,
        press_fn=_clock("business_trip_start"),
    ),
    TimebutlerButtonDescription(
        key="dienstreise_ende",
        entity_registry_enabled_default=False,
        available_fn=lambda clock: clock.clocked_in and clock.business_trip,
        press_fn=_clock("business_trip_end"),
    ),
)


class TimebutlerButton(TimebutlerEntity, ButtonEntity):
    """A time clock action; unavailable while the clock state does not allow it."""

    _platform_domain = "button"
    entity_description: TimebutlerButtonDescription

    def __init__(
        self,
        entry: TimebutlerConfigEntry,
        runtime: TimebutlerRuntime,
        description: TimebutlerButtonDescription,
    ) -> None:
        super().__init__(entry, runtime, description.key, ("live",))
        self.entity_description = description
        self._entry = entry

    @property
    def available(self) -> bool:
        data = self._runtime.live.data
        return (
            super().available
            and data is not None
            and data.clock is not None
            and self.entity_description.available_fn(data.clock)
        )

    async def async_press(self) -> None:
        await self.entity_description.press_fn(self.hass, self._entry)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TimebutlerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the time clock buttons."""
    async_add_entities(TimebutlerButton(entry, entry.runtime_data, description) for description in BUTTONS)
```

- [ ] **Step 4: Add the platform**

In `custom_components/timebutler/__init__.py`:

```python
PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SENSOR]
```

- [ ] **Step 5: Add button names**

In `strings.json` and `translations/en.json`, inside `"entity"` add (before `"binary_sensor"`):

```json
    "button": {
      "start": {"name": "Start"},
      "pause": {"name": "Pause"},
      "fortsetzen": {"name": "Resume"},
      "stopp": {"name": "Stop"},
      "stopp_pausenkorrektur": {"name": "Stop with break correction"},
      "verwerfen": {"name": "Discard"},
      "dienstreise_start": {"name": "Business trip start"},
      "dienstreise_ende": {"name": "Business trip end"}
    },
```

In `translations/de.json`:

```json
    "button": {
      "start": {"name": "Start"},
      "pause": {"name": "Pause"},
      "fortsetzen": {"name": "Fortsetzen"},
      "stopp": {"name": "Stopp"},
      "stopp_pausenkorrektur": {"name": "Stopp mit Pausenkorrektur"},
      "verwerfen": {"name": "Verwerfen"},
      "dienstreise_start": {"name": "Dienstreise Start"},
      "dienstreise_ende": {"name": "Dienstreise Ende"}
    },
```

Re-run the JSON validation command from Task 3 Step 9.

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_button.py -v`
Expected: all PASS.

- [ ] **Step 7: Run the whole suite**

Run: `pytest`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add custom_components/timebutler tests/test_button.py
git commit -m "feat: add time clock buttons"
```

---

### Task 5: Phase 1 docs, dashboard card and version

**Files:**
- Create: `dashboards/clock-buttons.yaml`
- Modify: `README.md`, `custom_components/timebutler/const.py`, `custom_components/timebutler/manifest.json`

**Interfaces:** none (docs and version only; `tests/test_manifest.py` checks the version stays in sync).

- [ ] **Step 1: Bump the version**

`const.py`: `VERSION = "0.2.0"`. `manifest.json`: `"version": "0.2.0"`.

- [ ] **Step 2: Create `dashboards/clock-buttons.yaml`**

```yaml
# Time clock buttons for the touch panel. Each button shows only while the
# clock state allows its action (unavailable buttons are hidden).
type: horizontal-stack
cards:
  - type: conditional
    conditions:
      - condition: state
        entity: button.timebutler_start
        state_not: unavailable
    card:
      type: button
      entity: button.timebutler_start
      name: Start
      icon: mdi:play
      tap_action:
        action: perform-action
        perform_action: button.press
        target:
          entity_id: button.timebutler_start
  - type: conditional
    conditions:
      - condition: state
        entity: button.timebutler_pause
        state_not: unavailable
    card:
      type: button
      entity: button.timebutler_pause
      name: Pause
      icon: mdi:pause
      tap_action:
        action: perform-action
        perform_action: button.press
        target:
          entity_id: button.timebutler_pause
  - type: conditional
    conditions:
      - condition: state
        entity: button.timebutler_fortsetzen
        state_not: unavailable
    card:
      type: button
      entity: button.timebutler_fortsetzen
      name: Fortsetzen
      icon: mdi:play-pause
      tap_action:
        action: perform-action
        perform_action: button.press
        target:
          entity_id: button.timebutler_fortsetzen
  - type: conditional
    conditions:
      - condition: state
        entity: button.timebutler_stopp
        state_not: unavailable
    card:
      type: button
      entity: button.timebutler_stopp
      name: Stopp
      icon: mdi:stop
      tap_action:
        action: perform-action
        perform_action: button.press
        target:
          entity_id: button.timebutler_stopp
```

- [ ] **Step 3: Update `README.md`**

Replace the first paragraph with:

```markdown
Custom integration that shows your own [Timebutler](https://timebutler.com) data in Home Assistant and lets you operate the time clock: clock state and controls, worked time today/this week/this month, vacation balance and upcoming absences. Uses the Timebutler API v2 with a personal access token.
```

After the sentence "The clock and today's values refresh every minute, …" insert:

````markdown
## Buttons

| Entity | Action | Usable while |
|---|---|---|
| `button.timebutler_start` | Start the clock | off |
| `button.timebutler_pause` | Pause | running |
| `button.timebutler_fortsetzen` | Resume | paused |
| `button.timebutler_stopp` | Stop and save | running, paused, waiting |
| `button.timebutler_stopp_pausenkorrektur` | Stop and accept a break-regulation correction | running, paused |
| `button.timebutler_verwerfen` | Discard the session (disabled by default) | not off |
| `button.timebutler_dienstreise_start` / `_ende` | Business trip start/end (disabled by default) | running or paused |

A button is *unavailable* while the clock state does not allow its action. [`dashboards/clock-buttons.yaml`](dashboards/clock-buttons.yaml) is a card that shows only the usable ones.

## Services

All services take an optional `config_entry_id`; it is only needed when several Timebutler accounts are set up. Errors (e.g. "terminal only", "clock already running") are shown as translated messages.

| Service | Fields | Response |
|---|---|---|
| `timebutler.start`, `.pause`, `.resume`, `.cancel`, `.business_trip_start`, `.business_trip_end` | – | – |
| `timebutler.stop` | `project`, `category` (name or ID; default: your Timebutler defaults), `remarks`, `break_regulation` (`apply`/`ignore`) | `outcome`, `id`, `gearbeitet`, `pause` |

### Break regulation

When a stop violates your company's break regulation, Timebutler does not save the entry. The integration then keeps the clock running, shows the required change as an error and as a persistent notification, and fires the event `timebutler_break_regulation` (`detail`, `obligatory`, `has_no_remedy`, `correction_work_minutes`, `correction_break_minutes`, `resulting_work_minutes`, `resulting_break_minutes`). Finish with *Stop with break correction* or `timebutler.stop` with `break_regulation: apply` (`ignore` saves unchanged and is refused for obligatory regulations).

### Example: clock out when leaving home

```yaml
automation:
  - alias: Ausstempeln beim Verlassen
    triggers:
      - trigger: state
        entity_id: person.me
        from: home
    conditions:
      - condition: state
        entity_id: binary_sensor.timebutler_eingestempelt
        state: "on"
    actions:
      - action: timebutler.stop
```
````

- [ ] **Step 4: Run the whole suite**

Run: `pytest`
Expected: all PASS (`test_manifest_version_matches_user_agent` included).

- [ ] **Step 5: Commit**

```bash
git add README.md dashboards/clock-buttons.yaml custom_components/timebutler/const.py custom_components/timebutler/manifest.json
git commit -m "docs: document time clock buttons and services, bump to 0.2.0"
```

---

# Phase 2 – Absences

### Task 6: API and model for absences

**Files:**
- Modify: `custom_components/timebutler/api.py`, `custom_components/timebutler/model.py`
- Create: `tests/fixtures/substitutes.json`, `tests/fixtures/absence_created.json`, `tests/fixtures/delete_result.json`
- Test: `tests/test_api.py`, `tests/test_model.py`

**Interfaces:**
- Consumes: Task 1 `_write`, `_get`; Task 2 `InputError`, `match_item`; existing `UPCOMING_STATUSES`.
- Produces:
  - `TimebutlerClient.get_substitutes() -> list[dict[str, Any]]`
  - `TimebutlerClient.create_absence(body: dict[str, Any]) -> dict[str, Any]`
  - `TimebutlerClient.delete_absence(absence_id: str, reason: str | None) -> dict[str, Any]` (ID URL-quoted; body `{"reason": …}` only when given)
  - `model.OVERTIME_TYPES = ("UEB", "AUA")`
  - `model.absence_type_code(value: str, types: dict[str, str]) -> str`
  - `model.absence_body(type_code: str, start: date, end: date | None, half_day: str | None, hours: float | None, comments: str | None) -> dict[str, Any]`
  - `model.find_absence(requests: list[dict[str, Any]], day: date) -> str`
  - `upcoming_absences` items gain key `"id"`.

- [ ] **Step 1: Add fixtures**

`tests/fixtures/substitutes.json`:
```json
[{"id": "42", "name": "Erika Musterfrau", "department": null}, {"id": "43", "name": "Hans Beispiel", "department": "IT"}]
```

`tests/fixtures/absence_created.json`:
```json
{"id": "6", "type": "URL", "startDate": "2026-11-09", "endDate": "2026-11-13", "days": 5.0, "hours": null, "status": "pending", "halfDay": null, "substitutePending": true, "medicalCertificateProvided": null}
```

`tests/fixtures/delete_result.json`:
```json
{"id": "3", "result": "cancellation_requested"}
```

- [ ] **Step 2: Write the failing API tests**

Append to `tests/test_api.py`:

```python
async def test_create_absence_posts_body(client, aioclient_mock):
    aioclient_mock.post(f"{API_BASE}/absence-requests", json=load_fixture_json("absence_created"))
    body = {"absenceTypeId": "URL", "startDate": "2026-11-09", "endDate": "2026-11-13"}

    assert (await client.create_absence(body))["id"] == "6"

    method, url, data, _ = aioclient_mock.mock_calls[0]
    assert method.upper() == "POST"
    assert data == body


async def test_delete_absence_with_and_without_reason(client, aioclient_mock):
    aioclient_mock.delete(f"{API_BASE}/absence-requests/3", json=load_fixture_json("delete_result"))

    assert (await client.delete_absence("3", "Projekt verschoben"))["result"] == "cancellation_requested"
    assert await client.delete_absence("3", None)

    assert [call[2] for call in aioclient_mock.mock_calls] == [{"reason": "Projekt verschoben"}, None]


async def test_delete_absence_quotes_the_id(client, aioclient_mock):
    aioclient_mock.delete(f"{API_BASE}/absence-requests/1%2F2", json={"id": "1/2", "result": "deleted"})

    await client.delete_absence("1/2", None)

    _, url, _, _ = aioclient_mock.mock_calls[0]
    assert url.raw_path.endswith("/absence-requests/1%2F2")


async def test_substitutes(client, aioclient_mock):
    aioclient_mock.get(f"{API_BASE}/substitute-employees", json=load_fixture_json("substitutes"))

    assert [s["id"] for s in await client.get_substitutes()] == ["42", "43"]
```

- [ ] **Step 3: Write the failing model tests**

Add `absence_body`, `absence_type_code`, `find_absence` to the model import in `tests/test_model.py` and append:

```python
def test_absence_type_by_code_or_name():
    assert absence_type_code("url", TYPES) == "URL"
    assert absence_type_code("Gleitzeit", TYPES) == "GLZ"


def test_absence_type_unknown():
    with pytest.raises(InputError) as err:
        absence_type_code("Sabbatical", TYPES)

    assert err.value.key == "unknown_absence_type"
    assert err.value.placeholders == {"name": "Sabbatical", "options": "Urlaub, Gleitzeit"}


def test_absence_body_full_days():
    body = absence_body("URL", date(2026, 11, 9), date(2026, 11, 13), None, None, "Herbst")

    assert body == {"absenceTypeId": "URL", "startDate": "2026-11-09", "endDate": "2026-11-13", "comments": "Herbst"}


def test_absence_body_single_half_day_defaults_end():
    body = absence_body("URL", date(2026, 11, 9), None, "afternoon", None, None)

    assert body == {"absenceTypeId": "URL", "startDate": "2026-11-09", "endDate": "2026-11-09", "halfDay": "afternoon"}


def test_absence_body_overtime_hours():
    body = absence_body("UEB", date(2026, 11, 9), None, None, 1.5, None)

    assert body["hours"] == "1.5"
    assert absence_body("AUA", date(2026, 11, 9), None, None, 8.0, None)["hours"] == "8"


@pytest.mark.parametrize(
    ("args", "key"),
    [
        (("URL", date(2026, 11, 9), date(2026, 11, 8), None, None), "end_before_start"),
        (("URL", date(2026, 11, 9), date(2026, 11, 10), "morning", None), "half_day_single_day"),
        (("UEB", date(2026, 11, 9), None, None, None), "hours_required"),
        (("UEB", date(2026, 11, 9), None, None, 0.0), "hours_required"),
        (("AUA", date(2026, 11, 9), date(2026, 11, 10), None, 2.0), "overtime_single_day"),
        (("URL", date(2026, 11, 9), None, None, 2.0), "hours_not_allowed"),
    ],
)
def test_absence_body_rejects(args, key):
    with pytest.raises(InputError) as err:
        absence_body(*args, None)

    assert err.value.key == key


def test_find_absence_covering_the_day():
    requests = load_fixture_json("absences")

    assert find_absence(requests, date(2026, 10, 28)) == "3"
    assert find_absence(requests, date(2026, 12, 21)) == "2"


def test_find_absence_ignores_rejected():
    with pytest.raises(InputError) as err:
        find_absence(load_fixture_json("absences"), date(2026, 11, 3))

    assert err.value.key == "absence_not_found"
    assert err.value.placeholders == {"date": "2026-11-03"}


def test_find_absence_ambiguous_half_days():
    requests = [
        {"id": "5", "startDate": "2026-11-20", "endDate": "2026-11-20", "status": "approved", "halfDay": "morning"},
        {"id": "9", "startDate": "2026-11-20", "endDate": "2026-11-20", "status": "pending", "halfDay": "afternoon"},
    ]

    with pytest.raises(InputError) as err:
        find_absence(requests, date(2026, 11, 20))

    assert err.value.key == "absence_ambiguous"
    assert err.value.placeholders == {"date": "2026-11-20", "ids": "5 (morning), 9 (afternoon)"}
```

And in `test_upcoming_filters_sorts_and_names` change the expected first item to include the ID:

```python
    assert result[0] == {
        "id": "3",
        "von": "2026-10-26",
        "bis": "2026-10-30",
        "art": "Urlaub",
        "tage": 5.0,
        "status": "approved",
        "halbtags": None,
    }
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `pytest tests/test_api.py tests/test_model.py -v`
Expected: ImportError for `absence_body` and AttributeError `create_absence`.

- [ ] **Step 5: Implement the API methods**

In `api.py` add `from urllib.parse import quote` to the imports and append to `TimebutlerClient`:

```python
    async def get_substitutes(self) -> list[dict[str, Any]]:
        """Employees that may substitute the user."""
        return await self._get("/substitute-employees")

    async def create_absence(self, body: dict[str, Any]) -> dict[str, Any]:
        """Submit an absence request; returns it in its new state."""
        return await self._write("POST", "/absence-requests", body)

    async def delete_absence(self, absence_id: str, reason: str | None) -> dict[str, Any]:
        """Delete an absence request, or ask for its cancellation."""
        body = {"reason": reason} if reason else None
        return await self._write("DELETE", f"/absence-requests/{quote(absence_id, safe='')}", body)
```

- [ ] **Step 6: Implement the model functions**

In `model.py`, in `upcoming_absences` add `"id": request.get("id"),` as the first key of the returned dict. Then add below `UPCOMING_STATUSES`:

```python
OVERTIME_TYPES = ("UEB", "AUA")
```

and append:

```python
def absence_type_code(value: str, types: dict[str, str]) -> str:
    """Absence type code from a code or a type name."""
    items = [{"id": code, "name": name} for code, name in types.items()]
    return match_item(value, items, "unknown_absence_type")


def absence_body(
    type_code: str,
    start: date,
    end: date | None,
    half_day: str | None,
    hours: float | None,
    comments: str | None,
) -> dict[str, Any]:
    """Checked body of POST /absence-requests."""
    end = end or start
    if end < start:
        raise InputError("end_before_start")
    if half_day and end != start:
        raise InputError("half_day_single_day")
    if type_code in OVERTIME_TYPES:
        if hours is None or hours <= 0:
            raise InputError("hours_required")
        if end != start:
            raise InputError("overtime_single_day")
    elif hours is not None:
        raise InputError("hours_not_allowed")
    body: dict[str, Any] = {
        "absenceTypeId": type_code,
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
    }
    if half_day:
        body["halfDay"] = half_day
    if hours is not None:
        body["hours"] = f"{hours:g}"
    if comments:
        body["comments"] = comments
    return body


def find_absence(requests: list[dict[str, Any]], day: date) -> str:
    """ID of the pending or approved request covering day."""
    key = day.isoformat()
    matches = [
        request
        for request in requests
        if request.get("status") in UPCOMING_STATUSES
        and isinstance(request.get("startDate"), str)
        and isinstance(request.get("endDate"), str)
        and request["startDate"] <= key <= request["endDate"]
    ]
    if not matches:
        raise InputError("absence_not_found", date=key)
    if len(matches) > 1:
        ids = ", ".join(
            f"{request.get('id')} ({request['halfDay']})" if request.get("halfDay") else str(request.get("id"))
            for request in matches
        )
        raise InputError("absence_ambiguous", date=key, ids=ids)
    return str(matches[0].get("id"))
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_api.py tests/test_model.py -v`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add custom_components/timebutler/api.py custom_components/timebutler/model.py tests/test_api.py tests/test_model.py tests/fixtures/substitutes.json tests/fixtures/absence_created.json tests/fixtures/delete_result.json
git commit -m "feat: add absence requests to the API client and model"
```

---

### Task 7: Absence services and ID attributes

**Files:**
- Modify: `custom_components/timebutler/actions.py`, `custom_components/timebutler/services.py`, `custom_components/timebutler/services.yaml`, `strings.json`, `translations/en.json`, `translations/de.json`, `README.md`
- Test: `tests/test_services.py`, `tests/test_init.py`

**Interfaces:**
- Consumes: Task 3 `call_api`, `checked_input`, `resolve_entry`, `BASE_SCHEMA`; Task 6 client methods and model functions; `background.data.absence_types: dict[str, str]`.
- Produces:
  - `actions.async_request_absence(hass, entry, *, absence_type: str, start: date, end: date | None = None, half_day: str | None = None, hours: float | None = None, comments: str | None = None, substitute: str | None = None) -> dict[str, Any]`
  - `actions.async_delete_absence(hass, entry, *, absence_id: str | None = None, day: date | None = None, reason: str | None = None) -> dict[str, Any]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_services.py` (add `import voluptuous as vol` to the imports):

```python
async def test_request_absence(hass, aioclient_mock, frozen_now):
    await setup_integration(hass, aioclient_mock)
    remock(aioclient_mock)
    aioclient_mock.get(f"{API_BASE}/substitute-employees", json=load_fixture_json("substitutes"))
    aioclient_mock.post(f"{API_BASE}/absence-requests", json=load_fixture_json("absence_created"))

    result = await _call(
        hass,
        "request_absence",
        {"type": "urlaub", "start_date": "2026-11-09", "end_date": "2026-11-13", "comments": "Herbst", "substitute": "erika musterfrau"},
        response=True,
    )

    assert result == {"id": "6", "status": "pending", "tage": 5.0}
    assert api_calls(aioclient_mock, "POST", "/absence-requests") == [
        {"absenceTypeId": "URL", "startDate": "2026-11-09", "endDate": "2026-11-13", "comments": "Herbst", "substituteEmployeeId": "42"}
    ]
    assert api_calls(aioclient_mock, "GET", "/absence-requests/my")


@pytest.mark.parametrize(
    ("data", "key"),
    [
        ({"type": "Sabbatical", "start_date": "2026-11-09"}, "unknown_absence_type"),
        ({"type": "URL", "start_date": "2026-11-09", "end_date": "2026-11-08"}, "end_before_start"),
        ({"type": "URL", "start_date": "2026-11-09", "substitute": "Niemand"}, "unknown_substitute"),
    ],
)
async def test_request_absence_rejects_bad_input(hass, aioclient_mock, frozen_now, data, key):
    await setup_integration(hass, aioclient_mock)
    aioclient_mock.get(f"{API_BASE}/substitute-employees", json=load_fixture_json("substitutes"))
    aioclient_mock.post(f"{API_BASE}/absence-requests", json=load_fixture_json("absence_created"))

    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "request_absence", data)

    assert err.value.translation_key == key
    assert api_calls(aioclient_mock, "POST", "/absence-requests") == []


async def test_request_absence_overlap(hass, aioclient_mock, frozen_now):
    await setup_integration(hass, aioclient_mock)
    aioclient_mock.post(f"{API_BASE}/absence-requests", status=409)

    with pytest.raises(HomeAssistantError) as err:
        await _call(hass, "request_absence", {"type": "URL", "start_date": "2026-10-27"})

    assert err.value.translation_key == "absence_overlap"


async def test_delete_absence_by_date(hass, aioclient_mock, frozen_now):
    await setup_integration(hass, aioclient_mock)
    remock(aioclient_mock)
    aioclient_mock.delete(f"{API_BASE}/absence-requests/3", json=load_fixture_json("delete_result"))

    result = await _call(hass, "delete_absence", {"date": "2026-10-28", "reason": "Projekt verschoben"}, response=True)

    assert result == {"id": "3", "result": "cancellation_requested"}
    assert api_calls(aioclient_mock, "DELETE", "/absence-requests/3") == [{"reason": "Projekt verschoben"}]


async def test_delete_absence_by_id(hass, aioclient_mock, frozen_now):
    await setup_integration(hass, aioclient_mock)
    aioclient_mock.delete(f"{API_BASE}/absence-requests/2", json={"id": "2", "result": "deleted"})

    assert await _call(hass, "delete_absence", {"id": "2"}, response=True) == {"id": "2", "result": "deleted"}
    assert api_calls(aioclient_mock, "DELETE", "/absence-requests/2") == [None]


async def test_delete_absence_without_match(hass, aioclient_mock, frozen_now):
    await setup_integration(hass, aioclient_mock)

    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "delete_absence", {"date": "2026-11-03"})

    assert err.value.translation_key == "absence_not_found"


async def test_delete_absence_needs_reason(hass, aioclient_mock, frozen_now):
    await setup_integration(hass, aioclient_mock)
    aioclient_mock.delete(f"{API_BASE}/absence-requests/3", status=400)

    with pytest.raises(HomeAssistantError) as err:
        await _call(hass, "delete_absence", {"id": "3"})

    assert err.value.translation_key == "absence_reason_required"


@pytest.mark.parametrize("data", [{}, {"id": "3", "date": "2026-10-28"}])
async def test_delete_absence_needs_exactly_id_or_date(hass, aioclient_mock, frozen_now, data):
    await setup_integration(hass, aioclient_mock)

    with pytest.raises((vol.Invalid, ServiceValidationError)):
        await _call(hass, "delete_absence", data)
```

In `tests/test_init.py`, inside `test_entities_report_live_and_background_data`, after the `absence.attributes["status"]` assertion add:

```python
    assert absence.attributes["id"] == "3"
    assert [a["id"] for a in absence.attributes["liste"]] == ["3", "5", "2"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_services.py tests/test_init.py -v`
Expected: new service tests FAIL with `ServiceNotFound`; the test_init change passes already (Task 6 added `id`) – that is fine.

- [ ] **Step 3: Add the actions**

In `actions.py` add `from datetime import date` to the imports, extend the model import with `absence_body`, `absence_type_code`, `find_absence`, `match_item`, and append:

```python
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
```

- [ ] **Step 4: Register the services**

In `services.py` extend the actions import with `async_delete_absence, async_request_absence`, add the schemas below `STOP_SCHEMA`:

```python
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
```

and at the end of `async_setup_services`:

```python
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
```

- [ ] **Step 5: Extend `services.yaml`**

Append:

```yaml
request_absence:
  fields:
    config_entry_id: *config_entry_id
    type:
      required: true
      example: Urlaub
      selector:
        text:
    start_date:
      required: true
      selector:
        date:
    end_date:
      selector:
        date:
    half_day:
      selector:
        select:
          options:
            - morning
            - afternoon
          translation_key: half_day
    hours:
      selector:
        number:
          min: 0
          max: 24
          step: 0.25
          mode: box
    comments:
      selector:
        text:
          multiline: true
    substitute:
      example: Erika Musterfrau
      selector:
        text:
delete_absence:
  fields:
    config_entry_id: *config_entry_id
    date:
      selector:
        date:
    id:
      selector:
        text:
    reason:
      selector:
        text:
          multiline: true
```

- [ ] **Step 6: Add translations**

`strings.json` and `translations/en.json` – add to `"services"`:

```json
    "request_absence": {
      "name": "Request absence",
      "description": "Submits an absence or vacation request.",
      "fields": {
        "config_entry_id": {"name": "Account", "description": "Timebutler account; only needed when several are set up."},
        "type": {"name": "Type", "description": "Absence type code (e.g. URL) or name (e.g. Urlaub)."},
        "start_date": {"name": "Start date", "description": "First day of the absence."},
        "end_date": {"name": "End date", "description": "Last day; defaults to the start date."},
        "half_day": {"name": "Half day", "description": "Morning or afternoon of a single day."},
        "hours": {"name": "Hours", "description": "Only for the overtime types UEB and AUA."},
        "comments": {"name": "Comment", "description": "Note visible to your managers."},
        "substitute": {"name": "Substitute", "description": "Name or employee ID of your substitute."}
      }
    },
    "delete_absence": {
      "name": "Delete absence",
      "description": "Deletes an absence request or asks for its cancellation.",
      "fields": {
        "config_entry_id": {"name": "Account", "description": "Timebutler account; only needed when several are set up."},
        "date": {"name": "Date", "description": "A day of the absence (pending or approved)."},
        "id": {"name": "ID", "description": "ID of the absence request, instead of the date."},
        "reason": {"name": "Reason", "description": "Reason for a cancellation request."}
      }
    }
```

add to `"selector"`:

```json
    "half_day": {
      "options": {
        "morning": "Morning",
        "afternoon": "Afternoon"
      }
    }
```

add to `"exceptions"`:

```json
    "unknown_absence_type": {"message": "Unknown absence type \"{name}\". Available: {options}"},
    "unknown_substitute": {"message": "Unknown substitute \"{name}\". Available: {options}"},
    "end_before_start": {"message": "The end date is before the start date."},
    "half_day_single_day": {"message": "A half day must be a single day."},
    "hours_required": {"message": "Overtime types (UEB, AUA) need hours greater than 0."},
    "overtime_single_day": {"message": "Overtime types (UEB, AUA) must be a single day."},
    "hours_not_allowed": {"message": "Hours are only allowed for the overtime types UEB and AUA."},
    "absence_invalid": {"message": "Timebutler rejected the absence request."},
    "absence_type_forbidden": {"message": "You may not enter this absence type."},
    "absence_overlap": {"message": "The days overlap an existing absence."},
    "absence_not_found": {"message": "No pending or approved absence on {date}."},
    "absence_ambiguous": {"message": "Several absences on {date}, pass the ID: {ids}"},
    "absence_unknown_id": {"message": "Timebutler knows no absence request with this ID."},
    "absence_reason_required": {"message": "Your company requires a reason for the cancellation."},
    "absence_delete_forbidden": {"message": "This absence request can neither be deleted nor cancelled."}
```

`translations/de.json` – add to `"services"`:

```json
    "request_absence": {
      "name": "Abwesenheit beantragen",
      "description": "Stellt einen Abwesenheits- oder Urlaubsantrag.",
      "fields": {
        "config_entry_id": {"name": "Konto", "description": "Timebutler-Konto; nur nötig, wenn mehrere eingerichtet sind."},
        "type": {"name": "Art", "description": "Code der Abwesenheitsart (z. B. URL) oder Name (z. B. Urlaub)."},
        "start_date": {"name": "Von", "description": "Erster Tag der Abwesenheit."},
        "end_date": {"name": "Bis", "description": "Letzter Tag; ohne Angabe der erste Tag."},
        "half_day": {"name": "Halbtags", "description": "Vormittag oder Nachmittag eines einzelnen Tages."},
        "hours": {"name": "Stunden", "description": "Nur für die Überstunden-Arten UEB und AUA."},
        "comments": {"name": "Kommentar", "description": "Notiz für deine Vorgesetzten."},
        "substitute": {"name": "Vertretung", "description": "Name oder Mitarbeiter-ID deiner Vertretung."}
      }
    },
    "delete_absence": {
      "name": "Abwesenheit löschen",
      "description": "Löscht einen Abwesenheitsantrag oder beantragt seine Stornierung.",
      "fields": {
        "config_entry_id": {"name": "Konto", "description": "Timebutler-Konto; nur nötig, wenn mehrere eingerichtet sind."},
        "date": {"name": "Datum", "description": "Ein Tag der Abwesenheit (offen oder genehmigt)."},
        "id": {"name": "ID", "description": "ID des Antrags, statt des Datums."},
        "reason": {"name": "Grund", "description": "Grund für eine Stornoanfrage."}
      }
    }
```

add to `"selector"`:

```json
    "half_day": {
      "options": {
        "morning": "Vormittag",
        "afternoon": "Nachmittag"
      }
    }
```

add to `"exceptions"`:

```json
    "unknown_absence_type": {"message": "Unbekannte Abwesenheitsart „{name}“. Verfügbar: {options}"},
    "unknown_substitute": {"message": "Unbekannte Vertretung „{name}“. Verfügbar: {options}"},
    "end_before_start": {"message": "Das Enddatum liegt vor dem Startdatum."},
    "half_day_single_day": {"message": "Halbe Tage gehen nur für einen einzelnen Tag."},
    "hours_required": {"message": "Überstunden-Arten (UEB, AUA) brauchen Stunden größer 0."},
    "overtime_single_day": {"message": "Überstunden-Arten (UEB, AUA) gehen nur für einen einzelnen Tag."},
    "hours_not_allowed": {"message": "Stunden sind nur bei den Überstunden-Arten UEB und AUA erlaubt."},
    "absence_invalid": {"message": "Timebutler hat den Abwesenheitsantrag abgelehnt."},
    "absence_type_forbidden": {"message": "Diese Abwesenheitsart darfst du nicht eintragen."},
    "absence_overlap": {"message": "Überschneidet sich mit einer bestehenden Abwesenheit."},
    "absence_not_found": {"message": "Keine offene oder genehmigte Abwesenheit am {date}."},
    "absence_ambiguous": {"message": "Mehrere Abwesenheiten am {date}, bitte die ID angeben: {ids}"},
    "absence_unknown_id": {"message": "Timebutler kennt keinen Abwesenheitsantrag mit dieser ID."},
    "absence_reason_required": {"message": "Für die Stornierung ist ein Grund erforderlich."},
    "absence_delete_forbidden": {"message": "Dieser Antrag kann weder gelöscht noch storniert werden."}
```

Re-run the JSON validation command from Task 3 Step 9.

- [ ] **Step 7: Document in `README.md`**

In the Entities table change the `sensor.timebutler_naechste_abwesenheit` row's meaning to: `Start date of the next approved or pending absence; attributes `id`, `art`, `bis`, `tage`, `status`, `halbtags`, `liste` (next 5, each with `id`)`.

In the Services table add:

```markdown
| `timebutler.request_absence` | `type` (code like `URL` or name like `Urlaub`), `start_date`, `end_date`, `half_day` (`morning`/`afternoon`), `hours` (only `UEB`/`AUA`), `comments`, `substitute` (name or ID) | `id`, `status`, `tage` |
| `timebutler.delete_absence` | `date` (a day of the absence) or `id`, `reason` | `id`, `result` (`deleted` or `cancellation_requested`) |
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `pytest`
Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add custom_components/timebutler README.md tests/test_services.py tests/test_init.py
git commit -m "feat: add services to request and delete absences"
```

---

# Phase 3 – Time entries

### Task 8: API and model for time entries

**Files:**
- Modify: `custom_components/timebutler/api.py`, `custom_components/timebutler/model.py`
- Create: `tests/fixtures/clock_entry.json`
- Test: `tests/test_api.py`, `tests/test_model.py`

**Interfaces:**
- Consumes: Task 1 `_write`; Task 2 `InputError`.
- Produces:
  - `TimebutlerClient.add_clock_entry(body: dict[str, Any]) -> dict[str, Any]`
  - `TimebutlerClient.delete_time_entry(entry_id: str) -> dict[str, Any]` (ID URL-quoted, no body)
  - `model.local_timestamp_ms(day: date, at: time, tz: tzinfo) -> int`
  - `model.time_entry_body(day: date, start: time, end: time, break_minutes: int, remarks: str | None, tz: tzinfo) -> dict[str, Any]`
  - `model.find_time_entry(entries: list[dict[str, Any]], day: date, start: time | None) -> str`

- [ ] **Step 1: Add fixture**

`tests/fixtures/clock_entry.json`:
```json
{"id": "10", "workedMinutes": 480, "breakMinutes": 30}
```

- [ ] **Step 2: Write the failing API tests**

Append to `tests/test_api.py`:

```python
async def test_add_clock_entry_posts_body(client, aioclient_mock):
    aioclient_mock.post(f"{API_BASE}/time-tracking/clock-entry", json=load_fixture_json("clock_entry"))
    body = {"startTimestamp": 1, "endTimestamp": 2, "pauseSeconds": 0}

    assert (await client.add_clock_entry(body))["id"] == "10"

    _, _, data, _ = aioclient_mock.mock_calls[0]
    assert data == body


async def test_delete_time_entry(client, aioclient_mock):
    aioclient_mock.delete(f"{API_BASE}/time-entries/9", json={"id": "9", "result": "deleted"})

    assert await client.delete_time_entry("9") == {"id": "9", "result": "deleted"}

    _, _, data, _ = aioclient_mock.mock_calls[0]
    assert data is None


async def test_delete_time_entry_quotes_the_id(client, aioclient_mock):
    aioclient_mock.delete(f"{API_BASE}/time-entries/..%2Fx", json={"id": "x", "result": "deleted"})

    await client.delete_time_entry("../x")

    _, url, _, _ = aioclient_mock.mock_calls[0]
    assert url.raw_path.endswith("/time-entries/..%2Fx")
```

- [ ] **Step 3: Write the failing model tests**

Add `find_time_entry`, `local_timestamp_ms`, `time_entry_body` to the model import in `tests/test_model.py`, add `time` to the `datetime` import (`from datetime import UTC, date, datetime, time`), and append:

```python
def test_local_timestamp_ms():
    assert local_timestamp_ms(date(2026, 10, 7), time(8, 0), BERLIN) == 1791352800000


def test_time_entry_body():
    body = time_entry_body(date(2026, 10, 7), time(8, 0), time(16, 30), 30, "Nachtrag", BERLIN)

    assert body == {
        "startTimestamp": 1791352800000,
        "endTimestamp": 1791383400000,
        "pauseSeconds": 1800,
        "remarks": "Nachtrag",
    }


def test_time_entry_body_on_dst_change_day():
    # 2026-10-25: clocks go back 03:00 -> 02:00, so 01:00-05:00 lasts 5 hours.
    body = time_entry_body(date(2026, 10, 25), time(1, 0), time(5, 0), 260, None, BERLIN)

    assert body["endTimestamp"] - body["startTimestamp"] == 300 * 60_000
    with pytest.raises(InputError) as err:
        time_entry_body(date(2026, 10, 25), time(1, 0), time(5, 0), 300, None, BERLIN)
    assert err.value.placeholders == {"minutes": "300"}


@pytest.mark.parametrize(
    ("start", "end", "break_minutes", "key"),
    [
        (time(16, 30), time(8, 0), 0, "end_not_after_start"),
        (time(8, 0), time(8, 0), 0, "end_not_after_start"),
        (time(8, 0), time(16, 30), 510, "break_too_long"),
    ],
)
def test_time_entry_body_rejects(start, end, break_minutes, key):
    with pytest.raises(InputError) as err:
        time_entry_body(date(2026, 10, 7), start, end, break_minutes, None, BERLIN)

    assert err.value.key == key


def test_find_time_entry():
    assert find_time_entry(ENTRIES, date(2026, 10, 7), None) == "9"
    assert find_time_entry(ENTRIES, date(2026, 10, 6), time(18, 0)) == "8"


def test_find_time_entry_ambiguous():
    with pytest.raises(InputError) as err:
        find_time_entry(ENTRIES, date(2026, 10, 6), None)

    assert err.value.key == "time_entry_ambiguous"
    assert err.value.placeholders == {"date": "2026-10-06", "times": "08:00, 18:00"}


def test_find_time_entry_missing():
    with pytest.raises(InputError) as err:
        find_time_entry(ENTRIES, date(2026, 10, 7), time(9, 0))

    assert err.value.key == "time_entry_not_found"
    assert err.value.placeholders == {"date": "2026-10-07"}
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `pytest tests/test_api.py tests/test_model.py -v`
Expected: ImportError `local_timestamp_ms` / AttributeError `add_clock_entry`.

- [ ] **Step 5: Implement the API methods**

Append to `TimebutlerClient`:

```python
    async def add_clock_entry(self, body: dict[str, Any]) -> dict[str, Any]:
        """Create a completed time entry from start/end timestamps."""
        return await self._write("POST", "/time-tracking/clock-entry", body)

    async def delete_time_entry(self, entry_id: str) -> dict[str, Any]:
        """Delete a time entry, or ask for its deletion."""
        return await self._write("DELETE", f"/time-entries/{quote(entry_id, safe='')}")
```

- [ ] **Step 6: Implement the model functions**

In `model.py` change the datetime import to `from datetime import UTC, date, datetime, time, timedelta, tzinfo` and append:

```python
def local_timestamp_ms(day: date, at: time, tz: tzinfo) -> int:
    """Unix milliseconds of a local wall-clock time."""
    return round(datetime.combine(day, at, tzinfo=tz).timestamp() * 1000)


def time_entry_body(
    day: date, start: time, end: time, break_minutes: int, remarks: str | None, tz: tzinfo
) -> dict[str, Any]:
    """Checked body of POST /time-tracking/clock-entry (without project/category)."""
    if end <= start:
        raise InputError("end_not_after_start")
    start_ms = local_timestamp_ms(day, start, tz)
    end_ms = local_timestamp_ms(day, end, tz)
    # Real duration, so a DST change on that day counts correctly.
    duration = (end_ms - start_ms) // 60_000
    if break_minutes < 0 or break_minutes >= duration:
        raise InputError("break_too_long", minutes=str(duration))
    body: dict[str, Any] = {
        "startTimestamp": start_ms,
        "endTimestamp": end_ms,
        "pauseSeconds": break_minutes * 60,
    }
    if remarks:
        body["remarks"] = remarks
    return body


def find_time_entry(entries: list[dict[str, Any]], day: date, start: time | None) -> str:
    """ID of the time entry on day (starting at start, when given)."""
    key = day.isoformat()
    matches = [entry for entry in entries if entry.get("date") == key]
    if start is not None:
        wanted = start.strftime("%H:%M")
        matches = [entry for entry in matches if entry.get("startTime") == wanted]
    if not matches:
        raise InputError("time_entry_not_found", date=key)
    if len(matches) > 1:
        times = ", ".join(sorted(str(entry.get("startTime") or "-") for entry in matches))
        raise InputError("time_entry_ambiguous", date=key, times=times)
    return str(matches[0].get("id"))
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_api.py tests/test_model.py -v`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add custom_components/timebutler/api.py custom_components/timebutler/model.py tests/test_api.py tests/test_model.py tests/fixtures/clock_entry.json
git commit -m "feat: add time entries to the API client and model"
```

---

### Task 9: Time entry services and final docs

**Files:**
- Modify: `custom_components/timebutler/actions.py`, `custom_components/timebutler/services.py`, `custom_components/timebutler/services.yaml`, `strings.json`, `translations/en.json`, `translations/de.json`, `README.md`
- Test: `tests/test_services.py`

**Interfaces:**
- Consumes: Task 3 `call_api`, `checked_input`, `async_assignments`, `resolve_entry`, `BASE_SCHEMA`; Task 8 client methods and model functions; `homeassistant.util.dt.get_default_time_zone()`.
- Produces:
  - `actions.async_add_time_entry(hass, entry, *, day: date, start: time, end: time, break_minutes: int = 0, project: str | None = None, category: str | None = None, remarks: str | None = None) -> dict[str, Any]`
  - `actions.async_delete_time_entry(hass, entry, *, time_entry_id: str | None = None, day: date | None = None, start: time | None = None) -> dict[str, Any]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_services.py`:

```python
async def test_add_time_entry(hass, aioclient_mock, frozen_now):
    await setup_integration(hass, aioclient_mock)
    remock(aioclient_mock)
    _mock_assignments(aioclient_mock)
    aioclient_mock.post(f"{API_BASE}/time-tracking/clock-entry", json=load_fixture_json("clock_entry"))

    result = await _call(
        hass,
        "add_time_entry",
        {"date": "2026-10-06", "start_time": "08:00", "end_time": "16:30", "break_minutes": 30, "project": "Intern", "remarks": "Nachtrag"},
        response=True,
    )

    assert result == {"id": "10", "gearbeitet": 480, "pause": 30}
    # The test instance runs in US/Pacific (UTC-7 in October).
    assert api_calls(aioclient_mock, "POST", "/time-tracking/clock-entry") == [
        {
            "startTimestamp": 1791298800000,
            "endTimestamp": 1791329400000,
            "pauseSeconds": 1800,
            "remarks": "Nachtrag",
            "projectId": 6,
            "categoryId": 0,
        }
    ]
    assert api_calls(aioclient_mock, "GET", "/time-tracking/work-summary")
    assert api_calls(aioclient_mock, "GET", "/time-entries/my")


async def test_add_time_entry_rejects_end_before_start(hass, aioclient_mock, frozen_now):
    await setup_integration(hass, aioclient_mock)

    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "add_time_entry", {"date": "2026-10-06", "start_time": "16:30", "end_time": "08:00"})

    assert err.value.translation_key == "end_not_after_start"


async def test_add_time_entry_forbidden(hass, aioclient_mock, frozen_now):
    await setup_integration(hass, aioclient_mock)
    _mock_assignments(aioclient_mock)
    aioclient_mock.post(f"{API_BASE}/time-tracking/clock-entry", status=403)

    with pytest.raises(HomeAssistantError) as err:
        await _call(hass, "add_time_entry", {"date": "2026-10-06", "start_time": "08:00", "end_time": "09:00"})

    assert err.value.translation_key == "time_entry_forbidden"


async def test_delete_time_entry_by_date_and_start(hass, aioclient_mock, frozen_now):
    await setup_integration(hass, aioclient_mock)
    remock(aioclient_mock)
    aioclient_mock.delete(f"{API_BASE}/time-entries/8", json={"id": "8", "result": "deleted"})

    result = await _call(hass, "delete_time_entry", {"date": "2026-10-06", "start_time": "18:00"}, response=True)

    assert result == {"id": "8", "result": "deleted"}
    assert api_calls(aioclient_mock, "DELETE", "/time-entries/8") == [None]
    fetched = [url for method, url, _, _ in aioclient_mock.mock_calls if method.upper() == "GET" and url.path.endswith("/time-entries/my")]
    assert fetched[0].query["year"] == "2026"
    assert fetched[0].query["month"] == "10"


async def test_delete_time_entry_ambiguous(hass, aioclient_mock, frozen_now):
    await setup_integration(hass, aioclient_mock)

    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "delete_time_entry", {"date": "2026-10-06"})

    assert err.value.translation_key == "time_entry_ambiguous"
    assert err.value.translation_placeholders == {"date": "2026-10-06", "times": "08:00, 18:00"}


async def test_delete_time_entry_in_closed_month(hass, aioclient_mock, frozen_now):
    await setup_integration(hass, aioclient_mock)
    aioclient_mock.delete(f"{API_BASE}/time-entries/9", status=409)

    with pytest.raises(HomeAssistantError) as err:
        await _call(hass, "delete_time_entry", {"id": "9"})

    assert err.value.translation_key == "month_closed"


@pytest.mark.parametrize(
    "data",
    [{}, {"id": "9", "date": "2026-10-07"}, {"id": "9", "start_time": "06:00"}],
)
async def test_delete_time_entry_needs_id_or_date(hass, aioclient_mock, frozen_now, data):
    await setup_integration(hass, aioclient_mock)

    with pytest.raises((vol.Invalid, ServiceValidationError)):
        await _call(hass, "delete_time_entry", data)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_services.py -v`
Expected: new tests FAIL with `ServiceNotFound`.

- [ ] **Step 3: Add the actions**

In `actions.py` change the datetime import to `from datetime import date, time`, add `from homeassistant.util import dt as dt_util`, extend the model import with `find_time_entry`, `time_entry_body`, and append:

```python
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
```

- [ ] **Step 4: Register the services**

In `services.py` extend the actions import with `async_add_time_entry, async_delete_time_entry`, add the schemas:

```python
ADD_TIME_ENTRY_SCHEMA = BASE_SCHEMA.extend(
    {
        vol.Required("date"): cv.date,
        vol.Required("start_time"): cv.time,
        vol.Required("end_time"): cv.time,
        vol.Optional("break_minutes", default=0): vol.All(vol.Coerce(int), vol.Range(min=0)),
        vol.Optional("project"): cv.string,
        vol.Optional("category"): cv.string,
        vol.Optional("remarks"): cv.string,
    }
)
DELETE_TIME_ENTRY_SCHEMA = vol.All(
    BASE_SCHEMA.extend(
        {
            vol.Optional("id"): cv.string,
            vol.Optional("date"): cv.date,
            vol.Optional("start_time"): cv.time,
        }
    ),
    cv.has_at_least_one_key("id", "date"),
    cv.has_at_most_one_key("id", "date"),
    cv.has_at_most_one_key("id", "start_time"),
)
```

and at the end of `async_setup_services`:

```python
    async def handle_add_time_entry(call: ServiceCall) -> ServiceResponse:
        return await async_add_time_entry(
            hass,
            resolve_entry(hass, call),
            day=call.data["date"],
            start=call.data["start_time"],
            end=call.data["end_time"],
            break_minutes=call.data["break_minutes"],
            project=call.data.get("project"),
            category=call.data.get("category"),
            remarks=call.data.get("remarks"),
        )

    async def handle_delete_time_entry(call: ServiceCall) -> ServiceResponse:
        return await async_delete_time_entry(
            hass,
            resolve_entry(hass, call),
            time_entry_id=call.data.get("id"),
            day=call.data.get("date"),
            start=call.data.get("start_time"),
        )

    hass.services.async_register(
        DOMAIN,
        "add_time_entry",
        handle_add_time_entry,
        schema=ADD_TIME_ENTRY_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "delete_time_entry",
        handle_delete_time_entry,
        schema=DELETE_TIME_ENTRY_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
```

- [ ] **Step 5: Extend `services.yaml`**

Append:

```yaml
add_time_entry:
  fields:
    config_entry_id: *config_entry_id
    date:
      required: true
      selector:
        date:
    start_time:
      required: true
      selector:
        time:
    end_time:
      required: true
      selector:
        time:
    break_minutes:
      default: 0
      selector:
        number:
          min: 0
          max: 720
          unit_of_measurement: min
          mode: box
    project:
      example: Kundenprojekt A
      selector:
        text:
    category:
      selector:
        text:
    remarks:
      selector:
        text:
          multiline: true
delete_time_entry:
  fields:
    config_entry_id: *config_entry_id
    date:
      selector:
        date:
    start_time:
      selector:
        time:
    id:
      selector:
        text:
```

- [ ] **Step 6: Add translations**

`strings.json` and `translations/en.json` – add to `"services"`:

```json
    "add_time_entry": {
      "name": "Add time entry",
      "description": "Adds a completed time entry (times in Home Assistant's time zone, same day).",
      "fields": {
        "config_entry_id": {"name": "Account", "description": "Timebutler account; only needed when several are set up."},
        "date": {"name": "Date", "description": "Day of the entry."},
        "start_time": {"name": "Start", "description": "Start time."},
        "end_time": {"name": "End", "description": "End time, after the start on the same day."},
        "break_minutes": {"name": "Break", "description": "Break within the entry, in minutes."},
        "project": {"name": "Project", "description": "Project name or ID; defaults to your default project."},
        "category": {"name": "Category", "description": "Category name or ID; defaults to your default category."},
        "remarks": {"name": "Remarks", "description": "Note for the time entry."}
      }
    },
    "delete_time_entry": {
      "name": "Delete time entry",
      "description": "Deletes a time entry or asks for its deletion.",
      "fields": {
        "config_entry_id": {"name": "Account", "description": "Timebutler account; only needed when several are set up."},
        "date": {"name": "Date", "description": "Day of the entry."},
        "start_time": {"name": "Start", "description": "Start time; needed when the day has several entries."},
        "id": {"name": "ID", "description": "ID of the time entry, instead of date and start."}
      }
    }
```

add to `"exceptions"`:

```json
    "end_not_after_start": {"message": "The end time must be after the start time (same day)."},
    "break_too_long": {"message": "The break must be shorter than the entry's {minutes} minutes."},
    "time_entry_invalid": {"message": "Timebutler rejected the time entry."},
    "time_entry_forbidden": {"message": "You may not create time entries."},
    "remarks_too_long": {"message": "The remarks are too long."},
    "time_entry_not_found": {"message": "No matching time entry on {date}."},
    "time_entry_ambiguous": {"message": "Several time entries on {date}, pass start_time: {times}"},
    "time_entry_unknown_id": {"message": "Timebutler knows no time entry with this ID."},
    "time_entry_delete_forbidden": {"message": "You may not delete this time entry."},
    "month_closed": {"message": "The month is closed; the entry can no longer be deleted."}
```

`translations/de.json` – add to `"services"`:

```json
    "add_time_entry": {
      "name": "Zeiteintrag nachtragen",
      "description": "Trägt einen abgeschlossenen Zeiteintrag nach (Zeiten in der Zeitzone von Home Assistant, am selben Tag).",
      "fields": {
        "config_entry_id": {"name": "Konto", "description": "Timebutler-Konto; nur nötig, wenn mehrere eingerichtet sind."},
        "date": {"name": "Datum", "description": "Tag des Eintrags."},
        "start_time": {"name": "Beginn", "description": "Startzeit."},
        "end_time": {"name": "Ende", "description": "Endzeit, nach dem Beginn am selben Tag."},
        "break_minutes": {"name": "Pause", "description": "Pause innerhalb des Eintrags in Minuten."},
        "project": {"name": "Projekt", "description": "Projektname oder ID; ohne Angabe dein Standardprojekt."},
        "category": {"name": "Kategorie", "description": "Kategoriename oder ID; ohne Angabe deine Standardkategorie."},
        "remarks": {"name": "Bemerkung", "description": "Notiz zum Zeiteintrag."}
      }
    },
    "delete_time_entry": {
      "name": "Zeiteintrag löschen",
      "description": "Löscht einen Zeiteintrag oder beantragt seine Löschung.",
      "fields": {
        "config_entry_id": {"name": "Konto", "description": "Timebutler-Konto; nur nötig, wenn mehrere eingerichtet sind."},
        "date": {"name": "Datum", "description": "Tag des Eintrags."},
        "start_time": {"name": "Beginn", "description": "Startzeit; nötig, wenn der Tag mehrere Einträge hat."},
        "id": {"name": "ID", "description": "ID des Zeiteintrags, statt Datum und Beginn."}
      }
    }
```

add to `"exceptions"`:

```json
    "end_not_after_start": {"message": "Die Endzeit muss nach der Startzeit liegen (am selben Tag)."},
    "break_too_long": {"message": "Die Pause muss kürzer sein als die {minutes} Minuten des Eintrags."},
    "time_entry_invalid": {"message": "Timebutler hat den Zeiteintrag abgelehnt."},
    "time_entry_forbidden": {"message": "Du darfst keine Zeiteinträge anlegen."},
    "remarks_too_long": {"message": "Die Bemerkung ist zu lang."},
    "time_entry_not_found": {"message": "Kein passender Zeiteintrag am {date}."},
    "time_entry_ambiguous": {"message": "Mehrere Zeiteinträge am {date}, bitte start_time angeben: {times}"},
    "time_entry_unknown_id": {"message": "Timebutler kennt keinen Zeiteintrag mit dieser ID."},
    "time_entry_delete_forbidden": {"message": "Diesen Zeiteintrag darfst du nicht löschen."},
    "month_closed": {"message": "Der Monat ist abgeschlossen, der Eintrag kann nicht mehr gelöscht werden."}
```

Re-run the JSON validation command from Task 3 Step 9.

- [ ] **Step 7: Final README**

Replace the first paragraph with:

```markdown
Custom integration that shows your own [Timebutler](https://timebutler.com) data in Home Assistant and lets you act on it: time clock state and controls, worked time today/this week/this month, vacation balance and upcoming absences, plus services to request or delete absences and to add or delete time entries. Uses the Timebutler API v2 with a personal access token.
```

In the Services table add:

```markdown
| `timebutler.add_time_entry` | `date`, `start_time`, `end_time` (same day, HA time zone), `break_minutes`, `project`, `category`, `remarks` | `id`, `gearbeitet`, `pause` |
| `timebutler.delete_time_entry` | `date` (+ `start_time` when the day has several entries) or `id` | `id`, `result` (`deleted` or `deletion_requested`) |
```

- [ ] **Step 8: Run the whole suite**

Run: `pytest`
Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add custom_components/timebutler README.md tests/test_services.py
git commit -m "feat: add services to add and delete time entries"
```
