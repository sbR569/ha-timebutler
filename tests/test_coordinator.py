"""Coordinators: data assembly, error handling, refresh after clock-out."""
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.const import CONF_API_TOKEN
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.common import MockConfigEntry

# Registers the flow handler; HA only starts reauth for domains that have one.
import custom_components.timebutler.config_flow  # noqa: F401
from custom_components.timebutler.api import TimebutlerClient
from custom_components.timebutler.const import DOMAIN
from custom_components.timebutler.coordinator import (
    LiveData,
    TimebutlerBackgroundCoordinator,
    TimebutlerLiveCoordinator,
)
from custom_components.timebutler.model import TodaySummary
from tests.common import mock_api

pytestmark = pytest.mark.usefixtures("auto_enable_custom_integrations")


@pytest.fixture
def entry(hass):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="4711", title="Max Mustermann", data={CONF_API_TOKEN: "tb_test"})
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def coordinators(hass, aioclient_mock, entry):
    client = TimebutlerClient(async_get_clientsession(hass), "tb_test")
    background = TimebutlerBackgroundCoordinator(hass, entry, client)
    live = TimebutlerLiveCoordinator(hass, entry, client, background)
    return live, background


async def test_live_combines_clock_and_summary(aioclient_mock, coordinators, frozen_now):
    live, _ = coordinators
    mock_api(aioclient_mock)

    await live.async_refresh()

    assert live.last_update_success
    assert live.data.clock.status == "running"
    assert live.data.today == TodaySummary(commitment=480, worked=240, remaining=240, percent=50, break_minutes=15)


async def test_live_does_not_count_the_session_twice(aioclient_mock, coordinators, frozen_now):
    live, background = coordinators
    mock_api(aioclient_mock, {"/time-tracking/work-summary": {"json": {"commitmentMinutes": 480, "workedMinutes": 240, "remainingMinutes": 240, "percentDone": 50, "breakMinutes": 15}}})
    await background.async_refresh()

    await live.async_refresh()

    assert live.data.today.worked == 240
    assert live.data.today.percent == 50


async def test_live_with_time_tracking_disabled(aioclient_mock, coordinators):
    live, _ = coordinators
    mock_api(aioclient_mock, {"/time-clock/status": {"status": 400}, "/time-tracking/work-summary": {"status": 400}})

    await live.async_refresh()

    assert live.last_update_success
    assert live.data == LiveData(clock=None, today=None)


async def test_live_connection_error_fails_update(aioclient_mock, coordinators):
    live, _ = coordinators
    mock_api(aioclient_mock, {"/time-clock/status": {"exc": aiohttp.ClientConnectionError()}})

    await live.async_refresh()

    assert not live.last_update_success


async def test_live_auth_error_starts_reauth(hass, aioclient_mock, coordinators):
    live, _ = coordinators
    mock_api(aioclient_mock, {"/time-clock/status": {"status": 401}})

    await live.async_refresh()
    await hass.async_block_till_done()

    assert not live.last_update_success
    flows = hass.config_entries.flow.async_progress()
    assert any(flow["context"]["source"] == SOURCE_REAUTH for flow in flows)


async def test_background_collects_data(aioclient_mock, coordinators, frozen_now):
    _, background = coordinators
    mock_api(aioclient_mock)

    await background.async_refresh()

    assert background.last_update_success
    assert background.data.time_off["vacationDaysRemaining"] == 14.0
    assert background.data.absence_types == {"URL": "Urlaub", "GLZ": "Gleitzeit"}
    assert len(background.data.absences) == 5
    assert len(background.data.entries) == 7


async def test_background_fetches_previous_month_when_week_starts_there(aioclient_mock, coordinators, freezer):
    _, background = coordinators
    freezer.move_to("2026-10-01T17:00:00+00:00")
    mock_api(aioclient_mock)

    await background.async_refresh()

    months = {
        (url.query["year"], url.query["month"])
        for _, url, _, _ in aioclient_mock.mock_calls
        if url.path.endswith("/time-entries/my")
    }
    assert months == {("2026", "10"), ("2026", "9")}
    assert len(background.data.entries) == 14


async def test_background_keeps_running_without_time_off_summary(aioclient_mock, coordinators, frozen_now):
    _, background = coordinators
    mock_api(aioclient_mock, {"/time-tracking/time-off-summary": {"status": 400}})

    await background.async_refresh()

    assert background.last_update_success
    assert background.data.time_off is None
    assert len(background.data.absences) == 5


async def test_clock_out_triggers_background_refresh(hass, aioclient_mock, coordinators, frozen_now):
    live, background = coordinators
    mock_api(aioclient_mock)
    await live.async_refresh()
    aioclient_mock.clear_requests()
    mock_api(aioclient_mock, {"/time-clock/status": "clock_idle"})

    with patch.object(background, "async_request_refresh", AsyncMock()) as refresh:
        await live.async_refresh()
        await hass.async_block_till_done()

    refresh.assert_awaited_once()


async def test_no_background_refresh_while_clocked_in(hass, aioclient_mock, coordinators, frozen_now):
    live, background = coordinators
    mock_api(aioclient_mock)
    await live.async_refresh()

    with patch.object(background, "async_request_refresh", AsyncMock()) as refresh:
        await live.async_refresh()
        await hass.async_block_till_done()

    refresh.assert_not_awaited()
