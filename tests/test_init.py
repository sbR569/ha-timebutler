"""Full setup: entities, availability, reauth, unload."""
from datetime import timedelta

import aiohttp
import pytest
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import CONF_API_TOKEN, STATE_UNAVAILABLE, STATE_UNKNOWN
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.timebutler.const import DOMAIN
from tests.common import mock_api

pytestmark = pytest.mark.usefixtures("auto_enable_custom_integrations")


async def _setup(hass, aioclient_mock, overrides=None) -> MockConfigEntry:
    mock_api(aioclient_mock, overrides)
    entry = MockConfigEntry(domain=DOMAIN, unique_id="4711", title="Max Mustermann", data={CONF_API_TOKEN: "tb_test"})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _state(hass, entity_id):
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    return state


async def test_entities_report_live_and_background_data(hass, aioclient_mock, frozen_now):
    entry = await _setup(hass, aioclient_mock)

    assert entry.state is ConfigEntryState.LOADED
    clock = _state(hass, "sensor.timebutler_stempeluhr")
    assert clock.state == "running"
    assert clock.attributes["seit"] == "2026-10-07T14:00:00+00:00"
    assert clock.attributes["dienstreise"] is False
    assert _state(hass, "binary_sensor.timebutler_eingestempelt").state == "on"
    assert _state(hass, "sensor.timebutler_arbeitszeit_heute").state == "240"
    assert _state(hass, "sensor.timebutler_soll_heute").state == "480"
    assert _state(hass, "sensor.timebutler_rest_heute").state == "240"
    assert _state(hass, "sensor.timebutler_fortschritt_heute").state == "50"
    assert _state(hass, "sensor.timebutler_pause_heute").state == "15"

    week = _state(hass, "sensor.timebutler_arbeitszeit_woche")
    assert week.state == "1190"
    assert week.attributes["tage"]["2026-10-05"] == 450
    assert week.attributes["tage"]["2026-10-07"] == 240
    month = _state(hass, "sensor.timebutler_arbeitszeit_monat")
    assert month.state == "2090"
    assert month.attributes["arbeitstage"] == 5

    vacation = _state(hass, "sensor.timebutler_urlaub_rest")
    assert vacation.state == "14.0"
    assert vacation.attributes["gesamt"] == 30.0
    assert vacation.attributes["genehmigt"] == 12.0
    assert vacation.attributes["beantragt"] == 4.0
    assert _state(hass, "sensor.timebutler_krankheitstage").state == "3.0"

    absence = _state(hass, "sensor.timebutler_naechste_abwesenheit")
    assert absence.state == "2026-10-26"
    assert absence.attributes["art"] == "Urlaub"
    assert absence.attributes["bis"] == "2026-10-30"
    assert absence.attributes["tage"] == 5.0
    assert absence.attributes["status"] == "approved"
    assert absence.attributes["id"] == "3"
    assert [a["id"] for a in absence.attributes["liste"]] == ["3", "5", "2"]
    assert [a["von"] for a in absence.attributes["liste"]] == ["2026-10-26", "2026-11-20", "2026-12-21"]


async def test_time_tracking_disabled_leaves_vacation_working(hass, aioclient_mock, frozen_now):
    await _setup(
        hass,
        aioclient_mock,
        {
            "/time-clock/status": {"status": 400},
            "/time-tracking/work-summary": {"status": 400},
            "/time-entries/my": {"json": []},
        },
    )

    assert _state(hass, "sensor.timebutler_stempeluhr").state == STATE_UNKNOWN
    assert _state(hass, "binary_sensor.timebutler_eingestempelt").state == STATE_UNKNOWN
    assert _state(hass, "sensor.timebutler_arbeitszeit_heute").state == STATE_UNKNOWN
    assert _state(hass, "sensor.timebutler_arbeitszeit_woche").state == "0"
    assert _state(hass, "sensor.timebutler_urlaub_rest").state == "14.0"


async def test_connection_error_marks_live_entities_unavailable(hass, aioclient_mock, frozen_now):
    await _setup(hass, aioclient_mock)
    aioclient_mock.clear_requests()
    mock_api(aioclient_mock, {"/time-clock/status": {"exc": aiohttp.ClientConnectionError()}})

    frozen_now.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert _state(hass, "sensor.timebutler_stempeluhr").state == STATE_UNAVAILABLE
    assert _state(hass, "binary_sensor.timebutler_eingestempelt").state == STATE_UNAVAILABLE
    assert _state(hass, "sensor.timebutler_urlaub_rest").state == "14.0"
    assert _state(hass, "sensor.timebutler_arbeitszeit_woche").state == "1190"


async def test_auth_error_after_setup_starts_reauth(hass, aioclient_mock, frozen_now):
    await _setup(hass, aioclient_mock)
    aioclient_mock.clear_requests()
    mock_api(aioclient_mock, {"/time-clock/status": {"status": 401}})

    frozen_now.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    flows = hass.config_entries.flow.async_progress()
    assert any(flow["context"]["source"] == SOURCE_REAUTH for flow in flows)


async def test_auth_error_on_setup(hass, aioclient_mock, frozen_now):
    entry = await _setup(hass, aioclient_mock, {"/time-tracking/time-off-summary": {"status": 401}})

    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(flow["context"]["source"] == SOURCE_REAUTH for flow in flows)


async def test_unload(hass, aioclient_mock, frozen_now):
    entry = await _setup(hass, aioclient_mock)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_bulky_attributes_are_not_recorded(hass, aioclient_mock, frozen_now):
    # Week/month day maps change every minute while clocked in; keeping them
    # out of the recorder avoids a new attributes row per minute.
    await _setup(hass, aioclient_mock)
    sensors = hass.data["entity_components"]["sensor"]

    for entity_id in ("sensor.timebutler_arbeitszeit_woche", "sensor.timebutler_arbeitszeit_monat", "sensor.timebutler_naechste_abwesenheit"):
        entity = sensors.get_entity(entity_id)
        assert {"tage", "liste"} <= entity._unrecorded_attributes


async def test_options_change_reloads_the_entry(hass, aioclient_mock, frozen_now):
    entry = await _setup(hass, aioclient_mock)
    ledger = entry.runtime_data.ledger
    assert not ledger.configured

    hass.config_entries.async_update_entry(entry, options={"balance_minutes": 30, "as_of": "2026-10-06"})
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.ledger is not ledger
    assert entry.runtime_data.ledger.balance_minutes == 30


async def test_unload_saves_and_remove_deletes_the_ledger(hass, aioclient_mock, hass_storage, frozen_now):
    entry = await _setup(hass, aioclient_mock)
    key = f"timebutler.{entry.entry_id}.overtime"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass_storage[key]["data"]["targets"] == {"2026-10-07": 480}

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert key not in hass_storage
