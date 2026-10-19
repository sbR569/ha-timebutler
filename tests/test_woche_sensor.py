"""Week sensor: day targets, absences, last week and the next free day."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.const import CONF_API_TOKEN
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.timebutler.const import DOMAIN
from custom_components.timebutler.model import holiday_dates
from custom_components.timebutler.overtime import storage_key
from custom_components.timebutler.sensor import SENSORS
from tests.common import entry_dto, load_fixture_json, mock_api

pytestmark = pytest.mark.usefixtures("auto_enable_custom_integrations")

ENTRY_ID = "01TESTENTRY"
KEY = storage_key(ENTRY_ID)
ENTITY = "sensor.timebutler_arbeitszeit_woche"
HOLIDAYS = "custom_components.timebutler.coordinator.holiday_dates"


@pytest.fixture
def thursday(freezer):
    """Thursday 2026-10-22, 10:00 in the test instance's time zone (US/Pacific)."""
    freezer.move_to("2026-10-22T17:00:00+00:00")
    return freezer


@pytest.fixture
def monday_in_november(freezer):
    """Monday 2026-11-02, 09:00 US/Pacific: last week lies in October."""
    freezer.move_to("2026-11-02T17:00:00+00:00")
    return freezer


@pytest.fixture
def new_years_day(freezer):
    """Thursday 2026-01-01, 09:00 US/Pacific: last week (2025-12-22..28) lies in the previous year."""
    freezer.move_to("2026-01-01T17:00:00+00:00")
    return freezer


def _store_targets(hass_storage, targets):
    hass_storage[KEY] = {
        "version": 1,
        "minor_version": 1,
        "key": KEY,
        "data": {
            "targets": targets,
            "frozen_through": None,
            "frozen_minutes": 0,
            "balance_minutes": None,
            "as_of": None,
        },
    }


async def _setup(hass, aioclient_mock, entries=None):
    mock_api(aioclient_mock, entries=entries)
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id=ENTRY_ID,
        unique_id="4711",
        title="Max Mustermann",
        data={CONF_API_TOKEN: "tb_test"},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _requested_months(aioclient_mock) -> set[tuple[str, str]]:
    return {
        (url.query["year"], url.query["month"])
        for _, url, _, _ in aioclient_mock.mock_calls
        if url.path.endswith("/time-entries/my")
    }


async def test_week_sensor_has_targets_and_last_week(hass, aioclient_mock, hass_storage, thursday):
    weekdays = [f"2026-10-{day}" for day in (12, 13, 14, 15, 16, 19, 20, 21, 22, 23)]
    _store_targets(hass_storage, dict.fromkeys(weekdays, 468))
    entries = load_fixture_json("entries_2026_10") + [
        entry_dto("2026-10-12", 300),
        entry_dto("2026-10-12", 150),
        entry_dto("2026-10-13", 60, "rejected"),
        entry_dto("2026-10-14", 480),
    ]

    await _setup(hass, aioclient_mock, {(2026, 10): entries})

    attributes = hass.states.get(ENTITY).attributes
    assert attributes["soll"]["2026-10-19"] == 468
    # Thursday is today: the live coordinator recorded Timebutler's 480. Friday follows the Friday norm.
    assert attributes["soll"] == {
        "2026-10-19": 468,
        "2026-10-20": 468,
        "2026-10-21": 468,
        "2026-10-22": 480,
        "2026-10-23": 468,
        "2026-10-24": 0,
        "2026-10-25": 0,
    }
    assert attributes["abwesend"] == {}
    assert attributes["vorwoche"]["tage"] == {
        "2026-10-12": 450,
        "2026-10-13": 0,  # the rejected entry does not count
        "2026-10-14": 480,
        "2026-10-15": 0,
        "2026-10-16": 0,
        "2026-10-17": 0,
        "2026-10-18": 0,
    }
    assert attributes["vorwoche"]["soll"] == {
        "2026-10-12": 468,
        "2026-10-13": 468,
        "2026-10-14": 468,
        "2026-10-15": 468,
        "2026-10-16": 468,
        "2026-10-17": 0,
        "2026-10-18": 0,
    }
    assert attributes["vorwoche"]["abwesend"] == {}
    assert attributes["naechster_freier_tag"] == "2026-10-24"
    assert attributes["naechster_freier_grund"] == "Wochenende"
    # The day map of this week is unchanged; today comes from the live summary.
    assert attributes["tage"]["2026-10-22"] == 240


async def test_week_sensor_lists_absences_and_holidays(hass, aioclient_mock, monday_in_november):
    await _setup(hass, aioclient_mock)

    attributes = hass.states.get(ENTITY).attributes
    # The rejected vacation request of 2026-11-02..06 is no absence.
    assert attributes["abwesend"] == {}
    assert attributes["vorwoche"]["abwesend"] == {
        **{f"2026-10-{day}": {"art": "Urlaub", "halbtags": False} for day in (26, 27, 28, 29, 30)},
        "2026-10-31": {"art": "Feiertag", "halbtags": False},
    }
    assert attributes["naechster_freier_tag"] == "2026-11-07"
    assert attributes["naechster_freier_grund"] == "Wochenende"


async def test_last_week_in_previous_month_is_fetched(hass, aioclient_mock, monday_in_november):
    entries = {
        (2026, 10): [entry_dto("2026-10-29", 200)],
        (2026, 11): [entry_dto("2026-11-01", 100)],
    }

    await _setup(hass, aioclient_mock, entries)

    assert _requested_months(aioclient_mock) == {("2026", "10"), ("2026", "11")}
    days = hass.states.get(ENTITY).attributes["vorwoche"]["tage"]
    assert list(days) == [
        "2026-10-26",
        "2026-10-27",
        "2026-10-28",
        "2026-10-29",
        "2026-10-30",
        "2026-10-31",
        "2026-11-01",
    ]
    assert days["2026-10-29"] == 200
    assert days["2026-11-01"] == 100


async def test_last_week_in_december_has_its_holidays(hass, aioclient_mock, hass_storage, new_years_day):
    # Timebutler reports a target of 0 on a public holiday.
    _store_targets(
        hass_storage,
        {"2025-12-22": 468, "2025-12-23": 468, "2025-12-24": 468, "2025-12-25": 0, "2025-12-26": 0},
    )

    await _setup(hass, aioclient_mock)

    attributes = hass.states.get(ENTITY).attributes
    holiday = {"art": "Feiertag", "halbtags": False}
    assert attributes["vorwoche"]["abwesend"] == {"2025-12-25": holiday, "2025-12-26": holiday}
    assert attributes["vorwoche"]["soll"] == {
        "2025-12-22": 468,
        "2025-12-23": 468,
        "2025-12-24": 468,
        "2025-12-25": 0,
        "2025-12-26": 0,
        "2025-12-27": 0,
        "2025-12-28": 0,
    }
    # Today is New Year's Day.
    assert attributes["abwesend"] == {"2026-01-01": holiday}


async def test_holidays_are_built_once_per_year(hass, aioclient_mock, frozen_now):
    with patch(HOLIDAYS, wraps=holiday_dates) as build:
        entry = await _setup(hass, aioclient_mock)
        background = entry.runtime_data.background
        await background.async_refresh()
        await background.async_refresh()

    build.assert_called_once_with((2025, 2026, 2027))


async def test_holidays_are_rebuilt_when_the_year_changes(hass, aioclient_mock, freezer):
    freezer.move_to("2026-12-31T17:00:00+00:00")
    with patch(HOLIDAYS, wraps=holiday_dates) as build:
        entry = await _setup(hass, aioclient_mock)
        freezer.move_to("2027-01-04T17:00:00+00:00")
        await entry.runtime_data.background.async_refresh()

    assert [call.args for call in build.call_args_list] == [((2025, 2026, 2027),), ((2026, 2027, 2028),)]


async def test_week_attributes_are_not_recorded(hass, aioclient_mock, frozen_now):
    await _setup(hass, aioclient_mock)

    entity = hass.data["entity_components"]["sensor"].get_entity(ENTITY)
    assert {
        "soll",
        "abwesend",
        "vorwoche",
        "naechster_freier_tag",
        "naechster_freier_grund",
    } <= entity._unrecorded_attributes


def test_week_attributes_without_background_data_are_only_the_day_map():
    description = next(item for item in SENSORS if item.key == "arbeitszeit_woche")
    runtime = SimpleNamespace(background=SimpleNamespace(data=None))

    assert description.attrs_fn(runtime) == {"tage": None}
