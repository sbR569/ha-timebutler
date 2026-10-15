"""Overtime balance sensor: start value, recorded targets, today, midnight."""
import pytest
from homeassistant.const import CONF_API_TOKEN, STATE_UNKNOWN
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.timebutler.const import DOMAIN
from custom_components.timebutler.overtime import storage_key
from tests.common import mock_api

pytestmark = pytest.mark.usefixtures("auto_enable_custom_integrations")

ENTRY_ID = "01TESTENTRY"
KEY = storage_key(ENTRY_ID)
ENTITY = "sensor.timebutler_ueberstunden"
START = {"balance_minutes": 90, "as_of": "2026-10-05"}


def _store_targets(hass_storage, targets):
    hass_storage[KEY] = {
        "version": 1,
        "minor_version": 1,
        "key": KEY,
        "data": {"targets": targets, "frozen_through": None, "frozen_minutes": 0, **START},
    }


async def _setup(hass, aioclient_mock, options=None, overrides=None):
    mock_api(aioclient_mock, overrides)
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


async def test_unknown_without_start_value(hass, aioclient_mock, frozen_now):
    await _setup(hass, aioclient_mock)

    state = hass.states.get(ENTITY)
    assert state.state == STATE_UNKNOWN
    assert "mit_heute" not in state.attributes


async def test_balance_up_to_yesterday_and_with_today(hass, aioclient_mock, hass_storage, frozen_now):
    _store_targets(hass_storage, {"2026-10-06": 480})

    await _setup(hass, aioclient_mock, START)

    state = hass.states.get(ENTITY)
    # 2026-10-06: 500 worked (the rejected 60 do not count) - 480 target
    assert state.state == "110"
    assert state.attributes["stand_vom"] == "2026-10-05"
    assert state.attributes["geschaetzt"] == []
    # today: 240 worked including the running session, target 480
    assert state.attributes["mit_heute"] == -130
    assert state.attributes["unit_of_measurement"] == "min"
    assert state.attributes["state_class"] == "measurement"


async def test_unseen_days_are_listed_as_estimated(hass, aioclient_mock, frozen_now):
    await _setup(hass, aioclient_mock, START)

    state = hass.states.get(ENTITY)
    assert state.state == str(90 + 500)  # no target known for 2026-10-06
    assert state.attributes["geschaetzt"] == ["2026-10-06"]


async def test_balance_without_time_tracking(hass, aioclient_mock, hass_storage, frozen_now):
    _store_targets(hass_storage, {"2026-10-06": 480})

    await _setup(
        hass,
        aioclient_mock,
        START,
        {"/time-clock/status": {"status": 400}, "/time-tracking/work-summary": {"status": 400}},
    )

    state = hass.states.get(ENTITY)
    assert state.state == "110"
    assert state.attributes["mit_heute"] is None


async def test_midnight_moves_today_into_the_balance(hass, aioclient_mock, hass_storage, freezer):
    freezer.move_to("2026-10-07T17:00:00+00:00")  # Wednesday 10:00 US/Pacific
    _store_targets(hass_storage, {"2026-10-06": 480})
    await _setup(hass, aioclient_mock, START)
    assert hass.states.get(ENTITY).state == "110"

    freezer.move_to("2026-10-08T07:05:00+00:00")  # Thursday 00:05 US/Pacific
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    # 2026-10-07: 60 minutes in the time entries - 480 recorded target
    assert hass.states.get(ENTITY).state == str(110 + 60 - 480)


async def test_changing_attributes_are_not_recorded(hass, aioclient_mock, frozen_now):
    await _setup(hass, aioclient_mock, START)

    entity = hass.data["entity_components"]["sensor"].get_entity(ENTITY)
    assert {"mit_heute", "geschaetzt"} <= entity._unrecorded_attributes
