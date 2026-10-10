"""TimebutlerClient: headers and error mapping."""
import asyncio

import aiohttp
import pytest

from custom_components.timebutler.api import (
    TimebutlerAuthError,
    TimebutlerClient,
    TimebutlerConnectionError,
    TimebutlerRequestError,
    TimebutlerTimeTrackingDisabled,
)
from custom_components.timebutler.const import API_BASE, USER_AGENT
from tests.common import load_fixture_json


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
