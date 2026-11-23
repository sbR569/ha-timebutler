"""Today's target, rest and progress follow approved absences; the ledger keeps Timebutler's value."""
import pytest
from homeassistant.const import CONF_API_TOKEN
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.timebutler.const import DOMAIN
from custom_components.timebutler.overtime import storage_key
from tests.common import mock_api

pytestmark = pytest.mark.usefixtures("auto_enable_custom_integrations")

ENTRY_ID = "01TESTENTRY"
KEY = storage_key(ENTRY_ID)
IDLE = {"/time-clock/status": "clock_idle"}
START = {"balance_minutes": 90, "as_of": "2026-10-05"}


@pytest.fixture
def vacation_tuesday(freezer):
    """Tuesday 2026-10-27, 10:00 US/Pacific: inside the approved vacation 2026-10-26..30."""
    freezer.move_to("2026-10-27T17:00:00+00:00")
    return freezer


@pytest.fixture
def half_day_friday(freezer):
    """Friday 2026-11-20, 10:00 US/Pacific: an approved half day."""
    freezer.move_to("2026-11-20T18:00:00+00:00")
    return freezer


async def _setup(hass, aioclient_mock, overrides=None, options=None):
    mock_api(aioclient_mock, {**IDLE, **(overrides or {})})
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id=ENTRY_ID,
        unique_id="4711",
        title="Max Mustermann",
        data={CONF_API_TOKEN: "tb_test"},
        options=options or {},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _today(hass) -> dict[str, str]:
    return {
        key: hass.states.get(f"sensor.timebutler_{key}_heute").state
        for key in ("arbeitszeit", "soll", "rest", "fortschritt")
    }


async def test_full_day_absence_has_no_target(hass, aioclient_mock, vacation_tuesday):
    # Timebutler keeps reporting 480 on a vacation day.
    entry = await _setup(hass, aioclient_mock)

    assert _today(hass) == {"arbeitszeit": "60", "soll": "0", "rest": "-60", "fortschritt": "0"}
    week = hass.states.get("sensor.timebutler_arbeitszeit_woche").attributes
    assert week["soll"]["2026-10-27"] == 0
    # The balance needs Timebutler's own value: an absence cancelled later must not leave a 0 behind.
    assert entry.runtime_data.ledger.targets["2026-10-27"] == 480


async def test_half_day_absence_halves_the_weekday_norm(hass, aioclient_mock, hass_storage, half_day_friday):
    hass_storage[KEY] = {
        "version": 1,
        "minor_version": 1,
        "key": KEY,
        "data": {"targets": {"2026-11-13": 330}, "frozen_through": None, "frozen_minutes": 0},
    }
    summary = {"commitmentMinutes": 330, "workedMinutes": 60, "remainingMinutes": 270, "percentDone": 18}

    await _setup(hass, aioclient_mock, {"/time-tracking/work-summary": {"json": summary}})

    assert _today(hass) == {"arbeitszeit": "60", "soll": "165", "rest": "105", "fortschritt": "36"}
    week = hass.states.get("sensor.timebutler_arbeitszeit_woche").attributes
    assert week["soll"]["2026-11-20"] == 165


async def test_balance_with_today_counts_the_absence_once(hass, aioclient_mock, half_day_friday):
    # No Friday norm yet: half of Timebutler's 480. Halving the shown 240 again would give -60.
    await _setup(hass, aioclient_mock, options=START)

    assert hass.states.get("sensor.timebutler_soll_heute").state == "240"
    state = hass.states.get("sensor.timebutler_ueberstunden")
    assert state.attributes["mit_heute"] - int(state.state) == 60 - 240


async def test_target_is_timebutlers_without_background_data(hass, aioclient_mock, vacation_tuesday):
    entry = await _setup(hass, aioclient_mock)
    runtime = entry.runtime_data

    runtime.background.data = None
    await runtime.live.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get("sensor.timebutler_soll_heute").state == "480"
