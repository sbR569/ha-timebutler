"""Time clock buttons: availability per clock state and pressing."""
import pytest
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.exceptions import HomeAssistantError
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


async def test_press_pause_refused_by_terminal_only(hass, aioclient_mock, frozen_now):
    await setup_integration(hass, aioclient_mock)
    aioclient_mock.post(f"{API_BASE}/time-clock/pause", status=403)

    with pytest.raises(HomeAssistantError) as err:
        await _press(hass, "pause")

    assert err.value.translation_key == "terminal_only"


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
