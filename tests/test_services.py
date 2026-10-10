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
