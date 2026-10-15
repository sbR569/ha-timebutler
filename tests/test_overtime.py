"""OvertimeLedger: store, start value, recorded targets, freezing."""
from datetime import date

from custom_components.timebutler.overtime import OvertimeLedger, storage_key
from tests.common import absence_dto, entry_dto

KEY = storage_key("entry1")


def _stored(hass_storage, **data):
    hass_storage[KEY] = {"version": 1, "minor_version": 1, "key": KEY, "data": data}


async def _ledger(hass, balance_minutes=None, as_of=None) -> OvertimeLedger:
    ledger = OvertimeLedger(hass, "entry1", balance_minutes, as_of)
    await ledger.async_load()
    return ledger


def test_storage_key():
    assert KEY == "timebutler.entry1.overtime"


async def test_unconfigured_ledger_has_no_balance(hass):
    ledger = await _ledger(hass)

    assert not ledger.configured
    assert ledger.counted_from() is None
    assert ledger.balance(date(2026, 10, 7), [], []) is None
    ledger.freeze(date(2026, 12, 1), [], [])
    assert ledger.frozen_through is None


async def test_balance_counts_closed_days_after_as_of(hass):
    ledger = await _ledger(hass, 90, date(2026, 10, 5))
    ledger.record_target(date(2026, 10, 6), 480)
    entries = [entry_dto("2026-10-05", 600), entry_dto("2026-10-06", 500), entry_dto("2026-10-07", 100)]

    # 2026-10-05 is covered by the start value, 2026-10-07 is today
    assert ledger.counted_from() == date(2026, 10, 6)
    assert ledger.balance(date(2026, 10, 7), entries, []) == (110, [])


async def test_balance_right_after_setting_the_start_value(hass):
    ledger = await _ledger(hass, -45, date(2026, 10, 6))

    assert ledger.balance(date(2026, 10, 7), [entry_dto("2026-10-07", 100)], []) == (-45, [])


async def test_old_start_date_lists_estimated_days(hass):
    ledger = await _ledger(hass, 0, date(2026, 10, 1))
    entries = [entry_dto("2026-10-02", 480), entry_dto("2026-10-05", 480), entry_dto("2026-10-06", 480)]

    total, estimated = ledger.balance(date(2026, 10, 7), entries, [])

    assert estimated == ["2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05", "2026-10-06"]
    assert total == 1440  # no target known for any of these days


async def test_record_target_and_save(hass, hass_storage):
    ledger = await _ledger(hass, 90, date(2026, 10, 5))
    ledger.record_target(date(2026, 10, 6), 480)
    ledger.record_target(date(2026, 10, 6), 360)

    await ledger.async_save()

    data = hass_storage[KEY]["data"]
    assert data["targets"] == {"2026-10-06": 360}
    assert data["balance_minutes"] == 90
    assert data["as_of"] == "2026-10-05"
    assert data["frozen_through"] is None
    assert data["frozen_minutes"] == 0


async def test_load_keeps_frozen_sum_for_same_start(hass, hass_storage):
    _stored(
        hass_storage,
        targets={"2026-10-06": 480},
        frozen_through="2026-08-31",
        frozen_minutes=-135,
        balance_minutes=90,
        as_of="2026-08-20",
    )

    ledger = await _ledger(hass, 90, date(2026, 8, 20))

    assert ledger.frozen_minutes == -135
    assert ledger.frozen_through == date(2026, 8, 31)
    assert ledger.counted_from() == date(2026, 9, 1)
    assert ledger.targets == {"2026-10-06": 480}


async def test_load_resets_frozen_sum_when_start_changed(hass, hass_storage):
    _stored(
        hass_storage,
        targets={"2026-10-06": 480},
        frozen_through="2026-08-31",
        frozen_minutes=-135,
        balance_minutes=90,
        as_of="2026-08-20",
    )

    ledger = await _ledger(hass, 120, date(2026, 8, 20))

    assert ledger.frozen_minutes == 0
    assert ledger.frozen_through is None
    assert ledger.counted_from() == date(2026, 8, 21)
    assert ledger.targets == {"2026-10-06": 480}


async def test_load_survives_garbage(hass, hass_storage):
    _stored(hass_storage, targets="nope", frozen_through="31.08.", frozen_minutes="x")

    ledger = await _ledger(hass, 0, date(2026, 10, 5))

    assert ledger.targets == {}
    assert ledger.frozen_through is None
    assert ledger.frozen_minutes == 0


async def test_freeze_at_month_change(hass):
    ledger = await _ledger(hass, 0, date(2026, 9, 28))
    ledger.record_target(date(2026, 9, 22), 300)  # Tuesday before the start, older norm
    ledger.record_target(date(2026, 9, 29), 480)  # Tuesday
    ledger.record_target(date(2026, 9, 30), 480)  # Wednesday
    ledger.record_target(date(2026, 10, 1), 480)
    entries = [entry_dto("2026-09-29", 500), entry_dto("2026-09-30", 450), entry_dto("2026-10-01", 500)]

    ledger.freeze(date(2026, 10, 15), entries, [])  # September is the previous month: nothing to freeze
    assert ledger.frozen_through is None

    ledger.freeze(date(2026, 11, 2), entries, [])  # September leaves the window
    assert ledger.frozen_through == date(2026, 9, 30)
    assert ledger.frozen_minutes == -10
    assert ledger.counted_from() == date(2026, 10, 1)
    # the older Tuesday is pruned, the latest Tuesday and Wednesday stay as weekday norms
    assert set(ledger.targets) == {"2026-09-29", "2026-09-30", "2026-10-01"}

    ledger.freeze(date(2026, 11, 20), entries, [])  # same month again: nothing new
    assert ledger.frozen_minutes == -10

    assert ledger.balance(date(2026, 10, 2), entries, []) == (-10 + 20, [])


async def test_today_difference_uses_absence_rules(hass):
    ledger = await _ledger(hass, 0, date(2026, 10, 5))
    ledger.record_target(date(2026, 10, 6), 480)  # Tuesday norm
    tuesday = date(2026, 10, 13)

    assert ledger.today_difference(tuesday, 300, 480, []) == -180
    half_day = [absence_dto("URL", "2026-10-13", half_day="morning")]
    assert ledger.today_difference(tuesday, 300, 480, half_day) == 60
    reduction = [absence_dto("AUA", "2026-10-13", hours=8)]
    assert ledger.today_difference(tuesday, 0, 0, reduction) == -480
    gain = [absence_dto("UEB", "2026-10-13", hours=1)]
    assert ledger.today_difference(tuesday, 480, 480, gain) == 60


async def test_remove_deletes_the_store(hass, hass_storage):
    _stored(hass_storage, targets={})

    await OvertimeLedger.async_remove(hass, "entry1")

    assert KEY not in hass_storage
