"""TimebutlerClient: headers and error mapping."""
import asyncio

import aiohttp
import pytest

from custom_components.timebutler.api import (
    TimebutlerAuthError,
    TimebutlerClient,
    TimebutlerConnectionError,
    TimebutlerTimeTrackingDisabled,
)
from custom_components.timebutler.const import API_BASE, USER_AGENT


@pytest.fixture
async def client(hass, aioclient_mock):
    session = aioclient_mock.create_session(hass.loop)
    yield TimebutlerClient(session, "tb_secret")
    await session.close()


async def test_sends_token_accept_and_user_agent(client, aioclient_mock):
    aioclient_mock.get(f"{API_BASE}/user/profile", json={"id": "4711"})

    assert await client.get_profile() == {"id": "4711"}

    _, url, _, headers = aioclient_mock.mock_calls[0]
    assert str(url) == f"{API_BASE}/user/profile"
    assert headers["Authorization"] == "Bearer tb_secret"
    assert headers["Accept"] == "application/json"
    assert headers["User-Agent"] == USER_AGENT


async def test_time_entries_pass_year_and_month(client, aioclient_mock):
    aioclient_mock.get(f"{API_BASE}/time-entries/my", json=[])

    assert await client.get_time_entries(2026, 9) == []

    _, url, _, _ = aioclient_mock.mock_calls[0]
    assert url.query["year"] == "2026"
    assert url.query["month"] == "9"


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"status": 401}, TimebutlerAuthError),
        ({"status": 403}, TimebutlerAuthError),
        ({"status": 400}, TimebutlerConnectionError),
        ({"status": 429}, TimebutlerConnectionError),
        ({"status": 500}, TimebutlerConnectionError),
        ({"exc": asyncio.TimeoutError()}, TimebutlerConnectionError),
        ({"exc": aiohttp.ClientConnectionError()}, TimebutlerConnectionError),
        ({"text": "<html>maintenance</html>"}, TimebutlerConnectionError),
    ],
)
async def test_error_mapping(client, aioclient_mock, kwargs, error):
    aioclient_mock.get(f"{API_BASE}/absence-types", **kwargs)

    with pytest.raises(error):
        await client.get_absence_types()


@pytest.mark.parametrize(
    "method",
    ["get_clock_status", "get_work_summary", "get_time_off_summary"],
)
async def test_400_on_time_tracking_endpoints_means_disabled(client, aioclient_mock, method):
    aioclient_mock.get(f"{API_BASE}/time-clock/status", status=400)
    aioclient_mock.get(f"{API_BASE}/time-tracking/work-summary", status=400)
    aioclient_mock.get(f"{API_BASE}/time-tracking/time-off-summary", status=400)

    with pytest.raises(TimebutlerTimeTrackingDisabled):
        await getattr(client, method)()
