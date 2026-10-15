# Overtime Balance (Überstunden-Saldo) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A sensor `sensor.timebutler_ueberstunden` with the working-time account balance: a start value entered once in the integration options, plus worked minus target time for every finished day, with absence, `UEB` and `AUA` rules.

**Architecture:** Pure day/balance arithmetic goes into `model.py`. A new `overtime.py` holds an `OvertimeLedger` that persists recorded daily targets and the frozen sum in a HA `Store`. The live coordinator records today's target into the ledger; the background coordinator fetches the months the balance needs and lets the ledger freeze old days. An options flow sets the start value; the sensor reads the ledger.

**Deviations from the spec (same behavior, implementation detail):**
- The store also keeps `balance_minutes` (not only `as_of`), so a changed balance with the same date also resets the frozen sum, as the spec's options section requires.
- `mit_heute` is unrecorded like `geschaetzt`: it changes every minute while clocked in and would otherwise write a recorder attributes row per minute.
- `geschaetzt` lists estimated days that are not frozen yet; once a day is frozen it is no longer listed.

**Tech Stack:** Home Assistant custom integration (Python 3.13+, `homeassistant.helpers.storage.Store`, voluptuous, selectors), pytest + pytest-homeassistant-custom-component (`hass`, `hass_storage`, `aioclient_mock`, `freezer`).

**Spec:** `docs/superpowers/specs/2026-10-15-ueberstunden-saldo-design.md`

## Global Constraints

- Home Assistant floor stays `2025.3.0` (`hacs.json`); `manifest.json` `requirements` stays `[]`.
- `model.py` must not import Home Assistant.
- Entity ID is fixed: `sensor.timebutler_ueberstunden` (key `ueberstunden`); unit `min`, device class duration, `state_class: measurement`.
- Options stored in `entry.options`: `balance_minutes` (int) and `as_of` (ISO date string). Form fields: `balance` (text) and `as_of` (date). Form errors: `invalid_balance`, `as_of_not_past`.
- Sensor attributes: `mit_heute` (int or None), `stand_vom` (ISO date), `geschaetzt` (list of ISO dates).
- Store key `timebutler.<entry_id>.overtime`, version 1.
- Only `approved` absence requests count. `UEB` = overtime worked (hours added), `AUA` = overtime reduction (target kept).
- `strings.json` and `translations/en.json` stay identical; `translations/de.json` has the same keys in German.
- Version becomes `0.3.0` in `const.VERSION` and `manifest.json` (Task 5).

## How to run tests

Home Assistant does not run on Windows. Run tests in WSL with the existing venv, from the checkout on branch `claude/ueberstunden-saldo`:

```bash
MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu -- bash -lc 'cd "/mnt/c/Users/begiers/GitProjects/ha-timebutler" && ~/.venvs/ha-zyxel-test/bin/python -m pytest -q -p no:cacheprovider tests/test_model.py'
```

Steps below write only the pytest arguments (e.g. "Run: `pytest tests/test_model.py -v`"); always run them through the WSL command above, replacing the trailing `tests/test_model.py` with the given arguments. If you work in a worktree, replace the `cd` path accordingly. Baseline before Task 1: `188 passed`.

## Review Focus

1. **Start date far in the past** (user enters "Stand vom" a week back): days before the first recorded target have no weekday norm, count with target 0 and must show up in `geschaetzt` so the user sees why the value is off. Test in Task 2 (`test_old_start_date_lists_estimated_days`).
2. **HA restart or entry reload during the day**: today's recorded target must survive; unloading must flush the store immediately, not after the 10 s save delay. Test in Task 3 (`test_unload_saves_and_remove_deletes_the_ledger`).
3. **Options saved again with unchanged values**: the frozen sum must stay; only a changed balance or date resets it. Test in Task 2 (`test_load_keeps_frozen_sum_for_same_start`).
4. **Time tracking disabled** (clock/work-summary answer 400): the sensor still shows the balance from recorded targets, `mit_heute` is None, nothing is recorded. Tests in Task 3 (`test_live_records_nothing_without_time_tracking`) and Task 5 (`test_balance_without_time_tracking`).
5. **Corrupt store file** (hand-edited or from a future version): setup must not fail; the ledger starts empty. Test in Task 2 (`test_load_survives_garbage`).

---

### Task 1: Balance arithmetic in `model.py`

**Files:**
- Modify: `custom_components/timebutler/model.py` (imports at the top; new code appended after `months_needed`)
- Modify: `tests/common.py` (two test data helpers)
- Test: `tests/test_model.py`

**Interfaces:**
- Consumes: `_entry_totals(entries) -> dict[str, int]` (existing, `model.py`), `_int` (existing).
- Produces (all in `custom_components/timebutler/model.py`):
  - `OVERTIME_GAIN = "UEB"`, `OVERTIME_REDUCTION = "AUA"`
  - `parse_balance(text: str) -> int` (raises `ValueError`)
  - `format_balance(minutes: int) -> str`
  - `@dataclass(frozen=True) DayAbsences(fraction: float = 0.0, reduction: bool = False, gain_minutes: int = 0)` with property `any -> bool`
  - `day_absences(requests: list[dict[str, Any]], day: date) -> DayAbsences`
  - `effective_target(raw: int, norm: int | None, absences: DayAbsences) -> int`
  - `weekday_norm(day: date, targets: dict[str, int], requests: list[dict[str, Any]]) -> int | None`
  - `overtime_days(first: date, last: date, entries: list[dict[str, Any]], requests: list[dict[str, Any]], targets: dict[str, int]) -> tuple[int, list[str]]`
  - `months_between(first: date, last: date) -> list[tuple[int, int]]`
  - In `tests/common.py`: `entry_dto(day: str, minutes: int, status: str = "done") -> dict` and `absence_dto(kind: str, start: str, end: str | None = None, *, status: str = "approved", half_day: str | None = None, hours: float | str | None = None) -> dict`

- [ ] **Step 1: Add the test data helpers to `tests/common.py`**

Append to `tests/common.py`:

```python
def entry_dto(day: str, minutes: int, status: str = "done") -> dict[str, Any]:
    """A minimal time entry as /time-entries/my returns it."""
    return {"id": f"{day}-{minutes}", "date": day, "workedMinutes": minutes, "status": status}


def absence_dto(
    kind: str,
    start: str,
    end: str | None = None,
    *,
    status: str = "approved",
    half_day: str | None = None,
    hours: float | str | None = None,
) -> dict[str, Any]:
    """A minimal absence request as /absence-requests/my returns it."""
    return {
        "id": f"{kind}-{start}",
        "type": kind,
        "startDate": start,
        "endDate": end or start,
        "status": status,
        "halfDay": half_day,
        "hours": hours,
    }
```

- [ ] **Step 2: Write the failing tests**

In `tests/test_model.py`, extend the import from `custom_components.timebutler.model` with `DayAbsences`, `day_absences`, `effective_target`, `format_balance`, `months_between`, `overtime_days`, `parse_balance`, `weekday_norm` (keep the list alphabetical as it is), change `from tests.common import load_fixture_json` to `from tests.common import absence_dto, entry_dto, load_fixture_json`, and append:

```python
@pytest.mark.parametrize(
    ("text", "minutes"),
    [
        ("+12:30", 750),
        ("-1:45", -105),
        ("12,5", 750),
        ("12.5", 750),
        ("-0:30", -30),
        (" 3 ", 180),
        ("0", 0),
        ("+0:05", 5),
        ("0,25", 15),
    ],
)
def test_parse_balance(text, minutes):
    assert parse_balance(text) == minutes


@pytest.mark.parametrize("text", ["", "abc", "1:60", "1:5", "--1", "+-1:00", "1,2,3", "nan", "inf", "1h"])
def test_parse_balance_rejects_garbage(text):
    with pytest.raises(ValueError):
        parse_balance(text)


def test_format_balance():
    assert format_balance(750) == "+12:30"
    assert format_balance(-105) == "-1:45"
    assert format_balance(0) == "+0:00"
    assert format_balance(-5) == "-0:05"


@pytest.mark.parametrize("minutes", [-1000, -1, 0, 59, 61, 6000])
def test_format_balance_round_trips(minutes):
    assert parse_balance(format_balance(minutes)) == minutes


def test_day_absences_counts_only_approved_requests_covering_the_day():
    requests = [
        absence_dto("URL", "2026-10-05", "2026-10-09"),
        absence_dto("URL", "2026-10-06", status="pending"),
        absence_dto("URL", "2026-10-06", status="rejected"),
    ]
    assert day_absences(requests, date(2026, 10, 6)) == DayAbsences(fraction=1.0)
    assert day_absences(requests, date(2026, 10, 10)) == DayAbsences()
    assert not DayAbsences().any


def test_day_absences_half_days_add_up_and_cap_at_one():
    morning = absence_dto("URL", "2026-10-06", half_day="morning")
    afternoon = absence_dto("GLZ", "2026-10-06", half_day="afternoon")
    full = absence_dto("GLZ", "2026-10-06")
    day = date(2026, 10, 6)
    assert day_absences([morning], day).fraction == 0.5
    assert day_absences([morning, afternoon], day).fraction == 1.0
    assert day_absences([full, morning], day).fraction == 1.0


def test_day_absences_overtime_types():
    requests = [
        absence_dto("UEB", "2026-10-06", hours=1.5),
        absence_dto("UEB", "2026-10-06", hours="0.25"),
        absence_dto("AUA", "2026-10-07", hours=8),
    ]
    assert day_absences(requests, date(2026, 10, 6)) == DayAbsences(gain_minutes=105)
    assert day_absences(requests, date(2026, 10, 7)) == DayAbsences(reduction=True)
    assert DayAbsences(gain_minutes=105).any
    assert DayAbsences(reduction=True).any


@pytest.mark.parametrize(
    ("raw", "norm", "absences", "expected"),
    [
        (480, 480, DayAbsences(), 480),
        (480, 480, DayAbsences(fraction=1.0), 0),
        (0, 480, DayAbsences(fraction=1.0), 0),  # Timebutler already set 0
        (480, 480, DayAbsences(fraction=0.5), 240),
        (240, 480, DayAbsences(fraction=0.5), 240),  # Timebutler already halved
        (480, None, DayAbsences(fraction=0.5), 240),  # no norm yet: half the raw target
        (480, 480, DayAbsences(reduction=True), 480),
        (0, 480, DayAbsences(reduction=True), 480),  # Timebutler lowered it: AUA still costs the day
        (0, None, DayAbsences(reduction=True), 0),
        (0, 480, DayAbsences(gain_minutes=60), 0),  # UEB does not change the target
    ],
)
def test_effective_target(raw, norm, absences, expected):
    assert effective_target(raw, norm, absences) == expected


def test_weekday_norm_uses_latest_earlier_same_weekday_without_absence():
    # Mondays 2026-09-28, 10-05, 10-12; Tuesday 10-13
    targets = {"2026-09-28": 480, "2026-10-05": 360, "2026-10-12": 0, "2026-10-13": 480}
    requests = [absence_dto("URL", "2026-10-12")]
    assert weekday_norm(date(2026, 10, 19), targets, requests) == 360  # 10-12 had vacation
    assert weekday_norm(date(2026, 10, 5), targets, requests) == 480  # only earlier days count
    assert weekday_norm(date(2026, 9, 28), targets, requests) is None
    assert weekday_norm(date(2026, 10, 20), targets, requests) == 480


def test_overtime_days_sums_worked_minus_target():
    entries = [
        entry_dto("2026-10-05", 500),
        entry_dto("2026-10-06", 400),
        entry_dto("2026-10-06", 60, status="rejected"),
        entry_dto("2026-10-07", 120),
    ]
    targets = {"2026-10-05": 480, "2026-10-06": 480, "2026-10-07": 480}
    assert overtime_days(date(2026, 10, 5), date(2026, 10, 6), entries, [], targets) == (-60, [])


def test_overtime_days_is_empty_when_first_is_after_last():
    assert overtime_days(date(2026, 10, 7), date(2026, 10, 6), [], [], {}) == (0, [])


def test_overtime_days_across_a_month_boundary():
    entries = [entry_dto("2026-09-30", 500), entry_dto("2026-10-01", 470)]
    targets = {"2026-09-30": 480, "2026-10-01": 480}
    assert overtime_days(date(2026, 9, 30), date(2026, 10, 1), entries, [], targets) == (10, [])


def test_overtime_days_marks_days_without_recorded_target():
    entries = [entry_dto("2026-10-12", 480), entry_dto("2026-10-13", 300)]
    targets = {"2026-10-05": 480, "2026-10-12": 480}  # Mondays known, Tuesday never seen
    assert overtime_days(date(2026, 10, 12), date(2026, 10, 13), entries, [], targets) == (300, ["2026-10-13"])


def test_overtime_days_estimates_from_weekday_norm():
    entries = [entry_dto("2026-10-13", 300)]
    targets = {"2026-10-06": 480}  # the Tuesday before
    assert overtime_days(date(2026, 10, 13), date(2026, 10, 13), entries, [], targets) == (-180, ["2026-10-13"])


def test_overtime_days_vacation_over_a_weekend_is_neutral():
    targets = {f"2026-10-{day:02d}": (0 if day in (10, 11) else 480) for day in range(5, 13)}
    requests = [absence_dto("URL", "2026-10-08", "2026-10-12")]  # Thursday to Monday
    entries = [entry_dto("2026-10-05", 480), entry_dto("2026-10-06", 480), entry_dto("2026-10-07", 540)]
    assert overtime_days(date(2026, 10, 5), date(2026, 10, 12), entries, requests, targets) == (60, [])


def test_overtime_days_with_overtime_absences():
    targets = {"2026-09-29": 480, "2026-10-06": 0, "2026-10-07": 480}
    requests = [absence_dto("AUA", "2026-10-06", hours=8), absence_dto("UEB", "2026-10-07", hours=2)]
    entries = [entry_dto("2026-10-07", 480)]
    # 10-06: AUA keeps the Tuesday norm 480 -> -480; 10-07: 480 - 480 + 120 -> +120
    assert overtime_days(date(2026, 10, 6), date(2026, 10, 7), entries, requests, targets) == (-360, [])


def test_months_between():
    assert months_between(date(2026, 11, 20), date(2027, 2, 1)) == [(2026, 11), (2026, 12), (2027, 1), (2027, 2)]
    assert months_between(date(2026, 10, 5), date(2026, 10, 7)) == [(2026, 10)]
    assert months_between(date(2026, 10, 8), date(2026, 10, 7)) == [(2026, 10)]
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest tests/test_model.py -v`
Expected: collection error, `ImportError: cannot import name 'DayAbsences'`.

- [ ] **Step 4: Implement**

In `custom_components/timebutler/model.py`, add `import re` to the standard-library imports (alphabetical: after `import math`). Append after `months_needed`:

```python
OVERTIME_GAIN = "UEB"
OVERTIME_REDUCTION = "AUA"

_BALANCE_HM = re.compile(r"([+-]?)(\d+):([0-5]\d)")
_BALANCE_HOURS = re.compile(r"([+-]?)(\d+(?:[.,]\d+)?)")


def parse_balance(text: str) -> int:
    """Minutes from '+12:30', '-1:45' or decimal hours '12,5' / '12.5'."""
    value = text.strip().replace(" ", "")
    if match := _BALANCE_HM.fullmatch(value):
        sign, hours, minutes = match.groups()
        total = int(hours) * 60 + int(minutes)
    elif match := _BALANCE_HOURS.fullmatch(value):
        sign, hours = match.groups()
        total = round(float(hours.replace(",", ".")) * 60)
    else:
        raise ValueError(f"not a balance: {text!r}")
    return -total if sign == "-" else total


def format_balance(minutes: int) -> str:
    """'+H:MM' or '-H:MM'."""
    hours, rest = divmod(abs(minutes), 60)
    return f"{'-' if minutes < 0 else '+'}{hours}:{rest:02d}"


@dataclass(frozen=True)
class DayAbsences:
    """Approved absences on one day, as far as the overtime balance cares."""

    fraction: float = 0.0  # ordinary absences: 1 per full day, 0.5 per half day, capped at 1
    reduction: bool = False  # AUA (overtime reduction) on this day
    gain_minutes: int = 0  # UEB (overtime worked) hours, in minutes

    @property
    def any(self) -> bool:
        """Whether any absence touches the day."""
        return self.fraction > 0 or self.reduction or self.gain_minutes > 0


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def day_absences(requests: list[dict[str, Any]], day: date) -> DayAbsences:
    """Combine the approved requests covering day."""
    key = day.isoformat()
    fraction = 0.0
    reduction = False
    gain_hours = 0.0
    for request in requests:
        start, end = request.get("startDate"), request.get("endDate")
        if (
            request.get("status") != "approved"
            or not isinstance(start, str)
            or not isinstance(end, str)
            or not start <= key <= end
        ):
            continue
        kind = request.get("type")
        if kind == OVERTIME_GAIN:
            gain_hours += _float(request.get("hours"))
        elif kind == OVERTIME_REDUCTION:
            reduction = True
        else:
            fraction += 0.5 if request.get("halfDay") else 1.0
    return DayAbsences(min(fraction, 1.0), reduction, round(gain_hours * 60))


def effective_target(raw: int, norm: int | None, absences: DayAbsences) -> int:
    """Target that counts for the balance, whether or not Timebutler already adjusted it."""
    base = raw if norm is None else norm
    if absences.fraction > 0:
        return min(raw, round(base * (1 - absences.fraction)))
    if absences.reduction:
        return max(raw, base)
    return raw


def _norms(targets: dict[str, int], requests: list[dict[str, Any]]) -> dict[int, list[tuple[str, int]]]:
    """Recorded targets of days without absence, per weekday, oldest first."""
    norms: dict[int, list[tuple[str, int]]] = {}
    for key in sorted(targets):
        day = date.fromisoformat(key)
        if not day_absences(requests, day).any:
            norms.setdefault(day.weekday(), []).append((key, targets[key]))
    return norms


def _norm_before(norms: dict[int, list[tuple[str, int]]], day: date) -> int | None:
    key = day.isoformat()
    value = None
    for other, minutes in norms.get(day.weekday(), []):
        if other >= key:
            break
        value = minutes
    return value


def weekday_norm(day: date, targets: dict[str, int], requests: list[dict[str, Any]]) -> int | None:
    """Recorded target of the latest earlier same weekday without absence."""
    return _norm_before(_norms(targets, requests), day)


def overtime_days(
    first: date,
    last: date,
    entries: list[dict[str, Any]],
    requests: list[dict[str, Any]],
    targets: dict[str, int],
) -> tuple[int, list[str]]:
    """Sum of worked minus target from first to last (inclusive), and the estimated days."""
    worked = _entry_totals(entries)
    norms = _norms(targets, requests)
    total = 0
    estimated: list[str] = []
    day = first
    while day <= last:
        key = day.isoformat()
        norm = _norm_before(norms, day)
        raw = targets.get(key)
        if raw is None:
            estimated.append(key)
            raw = norm or 0
        absences = day_absences(requests, day)
        total += worked.get(key, 0) - effective_target(raw, norm, absences) + absences.gain_minutes
        day += timedelta(days=1)
    return total, estimated


def months_between(first: date, last: date) -> list[tuple[int, int]]:
    """(year, month) from first's month through last's month; at least last's month."""
    months: list[tuple[int, int]] = []
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        months.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months or [(last.year, last.month)]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_model.py -v`
Expected: all PASS. Then run `pytest`: `188 passed` plus the new tests, no failures.

- [ ] **Step 6: Commit**

```bash
git add custom_components/timebutler/model.py tests/common.py tests/test_model.py
git commit -m "feat: add overtime balance arithmetic to the model"
```

---

### Task 2: `OvertimeLedger` in `overtime.py`

**Files:**
- Create: `custom_components/timebutler/overtime.py`
- Test: `tests/test_overtime.py`

**Interfaces:**
- Consumes (Task 1): `day_absences`, `effective_target`, `overtime_days`, `weekday_norm`; `DOMAIN` from `const.py`.
- Produces (`custom_components/timebutler/overtime.py`):
  - `STORAGE_VERSION = 1`, `SAVE_DELAY = 10`, `storage_key(entry_id: str) -> str` → `"timebutler.<entry_id>.overtime"`
  - `class OvertimeLedger(hass: HomeAssistant, entry_id: str, balance_minutes: int | None, as_of: date | None)` with attributes `balance_minutes`, `as_of`, `targets: dict[str, int]`, `frozen_through: date | None`, `frozen_minutes: int`, and:
    - `configured -> bool` (property)
    - `async async_load() -> None`
    - `record_target(day: date, minutes: int) -> None`
    - `counted_from() -> date | None`
    - `freeze(today: date, entries: list[dict], requests: list[dict]) -> None`
    - `balance(today: date, entries: list[dict], requests: list[dict]) -> tuple[int, list[str]] | None`
    - `today_difference(today: date, worked: int, target: int, requests: list[dict]) -> int`
    - `async async_save() -> None`
    - `@staticmethod async async_remove(hass: HomeAssistant, entry_id: str) -> None`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_overtime.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_overtime.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'custom_components.timebutler.overtime'`.

- [ ] **Step 3: Implement**

Create `custom_components/timebutler/overtime.py`:

```python
"""Overtime balance ledger: start value, recorded daily targets, frozen days."""

from __future__ import annotations

from datetime import date, timedelta
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .model import day_absences, effective_target, overtime_days, weekday_norm

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
SAVE_DELAY = 10


def storage_key(entry_id: str) -> str:
    """Store key of one config entry's ledger."""
    return f"{DOMAIN}.{entry_id}.overtime"


class OvertimeLedger:
    """What the overtime balance needs across restarts.

    The start value (balance at the end of `as_of`) comes from the entry
    options; the store keeps the daily targets the live coordinator saw and
    the sum of days that are no longer fetched (frozen).
    """

    def __init__(
        self, hass: HomeAssistant, entry_id: str, balance_minutes: int | None, as_of: date | None
    ) -> None:
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, storage_key(entry_id))
        self.balance_minutes = balance_minutes
        self.as_of = as_of
        self.targets: dict[str, int] = {}
        self.frozen_through: date | None = None
        self.frozen_minutes = 0

    @property
    def configured(self) -> bool:
        """Whether a start value is set."""
        return self.balance_minutes is not None and self.as_of is not None

    async def async_load(self) -> None:
        """Read the store; forget frozen days when the start value changed."""
        try:
            data = await self._store.async_load() or {}
            targets = {str(key): int(value) for key, value in data.get("targets", {}).items()}
            frozen = data.get("frozen_through")
            frozen_through = date.fromisoformat(frozen) if frozen else None
            frozen_minutes = int(data.get("frozen_minutes", 0))
            stored_start = (data.get("balance_minutes"), data.get("as_of"))
        except (AttributeError, HomeAssistantError, TypeError, ValueError) as err:
            _LOGGER.warning("Ignoring the unreadable overtime store: %s", err)
            targets, frozen_through, frozen_minutes, stored_start = {}, None, 0, (None, None)
        self.targets = targets
        current_start = (self.balance_minutes, self.as_of.isoformat() if self.as_of else None)
        if stored_start == current_start:
            self.frozen_through, self.frozen_minutes = frozen_through, frozen_minutes
        else:
            self.frozen_through, self.frozen_minutes = None, 0
            self._schedule_save()

    def record_target(self, day: date, minutes: int) -> None:
        """Remember Timebutler's target for day (the latest value wins)."""
        key = day.isoformat()
        if self.targets.get(key) != minutes:
            self.targets[key] = minutes
            self._schedule_save()

    def counted_from(self) -> date | None:
        """First day that is neither covered by the start value nor frozen."""
        if self.as_of is None or self.balance_minutes is None:
            return None
        done = max(self.as_of, self.frozen_through) if self.frozen_through else self.as_of
        return done + timedelta(days=1)

    def freeze(self, today: date, entries: list[dict[str, Any]], requests: list[dict[str, Any]]) -> None:
        """Sum up the days before the previous month; they are no longer fetched."""
        start = self.counted_from()
        if start is None:
            return
        last = today.replace(day=1) - timedelta(days=1)  # end of the previous month
        last = last.replace(day=1) - timedelta(days=1)  # end of the month before that
        if start > last:
            return
        total, _ = overtime_days(start, last, entries, requests, self.targets)
        self.frozen_minutes += total
        self.frozen_through = last
        self._prune(requests)
        self._schedule_save()

    def _prune(self, requests: list[dict[str, Any]]) -> None:
        """Drop frozen targets except the latest per weekday without absence (the norms)."""
        assert self.frozen_through is not None
        cutoff = self.frozen_through.isoformat()
        norms: dict[int, str] = {}
        for key in sorted(self.targets):
            day = date.fromisoformat(key)
            if key <= cutoff and not day_absences(requests, day).any:
                norms[day.weekday()] = key
        keep = set(norms.values())
        self.targets = {key: value for key, value in self.targets.items() if key > cutoff or key in keep}

    def balance(
        self, today: date, entries: list[dict[str, Any]], requests: list[dict[str, Any]]
    ) -> tuple[int, list[str]] | None:
        """Balance at the end of yesterday, and the days with an estimated target."""
        start = self.counted_from()
        if start is None or self.balance_minutes is None:
            return None
        total, estimated = overtime_days(start, today - timedelta(days=1), entries, requests, self.targets)
        return self.balance_minutes + self.frozen_minutes + total, estimated

    def today_difference(self, today: date, worked: int, target: int, requests: list[dict[str, Any]]) -> int:
        """Today's worked minus target with the absence rules, from live values."""
        absences = day_absences(requests, today)
        norm = weekday_norm(today, self.targets, requests)
        return worked - effective_target(target, norm, absences) + absences.gain_minutes

    async def async_save(self) -> None:
        """Write the store now."""
        await self._store.async_save(self._data())

    @staticmethod
    async def async_remove(hass: HomeAssistant, entry_id: str) -> None:
        """Delete a removed entry's store file."""
        await Store(hass, STORAGE_VERSION, storage_key(entry_id)).async_remove()

    def _schedule_save(self) -> None:
        self._store.async_delay_save(self._data, SAVE_DELAY)

    def _data(self) -> dict[str, Any]:
        return {
            "targets": self.targets,
            "frozen_through": self.frozen_through.isoformat() if self.frozen_through else None,
            "frozen_minutes": self.frozen_minutes,
            "balance_minutes": self.balance_minutes,
            "as_of": self.as_of.isoformat() if self.as_of else None,
        }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_overtime.py -v`
Expected: all PASS. Then `pytest`: no failures.

- [ ] **Step 5: Commit**

```bash
git add custom_components/timebutler/overtime.py tests/test_overtime.py
git commit -m "feat: add the overtime ledger with stored targets and frozen days"
```

---

### Task 3: Wire the ledger into setup and coordinators

**Files:**
- Modify: `custom_components/timebutler/const.py` (option keys)
- Modify: `custom_components/timebutler/coordinator.py`
- Modify: `custom_components/timebutler/__init__.py`
- Test: `tests/test_coordinator.py`, `tests/test_init.py`

**Interfaces:**
- Consumes (Task 1): `months_between`; (Task 2): `OvertimeLedger`.
- Produces:
  - `const.OPT_BALANCE_MINUTES = "balance_minutes"`, `const.OPT_AS_OF = "as_of"`
  - `TimebutlerBackgroundCoordinator(hass, entry, client, ledger: OvertimeLedger)` with attribute `ledger`
  - `TimebutlerRuntime(live, background, ledger)` dataclass field `ledger: OvertimeLedger`
  - Live coordinator records `today.commitment` for today's date after each successful update with time tracking on.
  - Entry reloads when its options change; unloading saves the ledger; `async_remove_entry` deletes the store.

- [ ] **Step 1: Write the failing coordinator tests**

In `tests/test_coordinator.py`:

Add the imports `from datetime import date` (first line of the imports) and `from custom_components.timebutler.overtime import OvertimeLedger`.

Replace the `coordinators` fixture with:

```python
@pytest.fixture
async def ledger(hass, entry):
    ledger = OvertimeLedger(hass, entry.entry_id, None, None)
    await ledger.async_load()
    return ledger


@pytest.fixture
def coordinators(hass, aioclient_mock, entry, ledger):
    client = TimebutlerClient(async_get_clientsession(hass), "tb_test")
    background = TimebutlerBackgroundCoordinator(hass, entry, client, ledger)
    live = TimebutlerLiveCoordinator(hass, entry, client, background)
    return live, background
```

Append:

```python
def _requested_months(aioclient_mock) -> set[tuple[str, str]]:
    return {
        (url.query["year"], url.query["month"])
        for _, url, _, _ in aioclient_mock.mock_calls
        if url.path.endswith("/time-entries/my")
    }


async def test_live_records_todays_target(aioclient_mock, coordinators, ledger, frozen_now):
    live, background = coordinators
    mock_api(aioclient_mock)

    await background.async_refresh()
    await live.async_refresh()

    assert ledger.targets == {"2026-10-07": 480}


async def test_live_records_nothing_without_time_tracking(aioclient_mock, coordinators, ledger, frozen_now):
    live, _ = coordinators
    mock_api(aioclient_mock, {"/time-clock/status": {"status": 400}, "/time-tracking/work-summary": {"status": 400}})

    await live.async_refresh()

    assert ledger.targets == {}


async def test_background_fetches_only_the_usual_months_without_start(aioclient_mock, coordinators, frozen_now):
    _, background = coordinators
    mock_api(aioclient_mock)

    await background.async_refresh()

    assert _requested_months(aioclient_mock) == {("2026", "10")}


async def test_background_fetches_months_since_start_and_freezes(aioclient_mock, coordinators, ledger, frozen_now):
    _, background = coordinators
    ledger.balance_minutes = 0
    ledger.as_of = date(2026, 8, 20)
    mock_api(aioclient_mock)

    await background.async_refresh()

    assert background.last_update_success
    assert _requested_months(aioclient_mock) == {("2026", "8"), ("2026", "9"), ("2026", "10")}
    assert ledger.frozen_through == date(2026, 8, 31)

    aioclient_mock.clear_requests()
    mock_api(aioclient_mock)
    await background.async_refresh()

    assert _requested_months(aioclient_mock) == {("2026", "9"), ("2026", "10")}
```

- [ ] **Step 2: Write the failing setup tests**

In `tests/test_init.py`, append:

```python
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
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest tests/test_coordinator.py tests/test_init.py -v`
Expected: the new coordinator tests and every test using the `coordinators` fixture FAIL with `TypeError: TimebutlerBackgroundCoordinator.__init__() takes 4 positional arguments but 5 were given`; the two new `test_init.py` tests FAIL with `AttributeError: 'TimebutlerRuntime' object has no attribute 'ledger'`.

- [ ] **Step 4: Add the option keys to `const.py`**

Append to `custom_components/timebutler/const.py`:

```python

# Entry options: overtime balance start value (minutes) valid at the end of as_of (ISO date).
OPT_BALANCE_MINUTES = "balance_minutes"
OPT_AS_OF = "as_of"
```

- [ ] **Step 5: Change `coordinator.py`**

1. Extend the model import with `months_between`, and add `from .overtime import OvertimeLedger` after the `.model` import block.

2. Replace `TimebutlerBackgroundCoordinator.__init__` and `_async_update_data` with:

```python
    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: TimebutlerClient, ledger: OvertimeLedger
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} background",
            update_interval=BACKGROUND_INTERVAL,
        )
        self.client = client
        self.ledger = ledger

    def _months(self, today: date) -> list[tuple[int, int]]:
        """The week/month sensors' months plus every month the balance still counts."""
        months = months_needed(today)
        counted_from = self.ledger.counted_from()
        if counted_from is not None:
            months += [month for month in months_between(counted_from, today) if month not in months]
        return months

    async def _async_update_data(self) -> BackgroundData:
        today = dt_util.now().date()
        try:
            try:
                time_off = await self.client.get_time_off_summary()
            except TimebutlerTimeTrackingDisabled:
                time_off = None
            absences = await self.client.get_absence_requests()
            types = {
                str(item.get("id")): str(item.get("name"))
                for item in await self.client.get_absence_types()
            }
            entries: list[dict[str, Any]] = []
            for year, month in self._months(today):
                entries.extend(await self.client.get_time_entries(year, month))
        except TimebutlerAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except TimebutlerConnectionError as err:
            raise UpdateFailed(str(err)) from err
        self.ledger.freeze(today, entries, absences)
        return BackgroundData(time_off=time_off, absences=absences, absence_types=types, entries=entries)
```

Add `from datetime import date` to the imports (after `from dataclasses import dataclass`).

3. In `TimebutlerLiveCoordinator._async_update_data`, replace the final `return LiveData(...)` block with:

```python
        today = today_summary(summary, clock, includes_running, now)
        self._background.ledger.record_target(now.date(), today.commitment)
        return LiveData(clock=clock, today=today)
```

4. Extend `TimebutlerRuntime`:

```python
@dataclass
class TimebutlerRuntime:
    """Coordinators and overtime ledger of one config entry."""

    live: TimebutlerLiveCoordinator
    background: TimebutlerBackgroundCoordinator
    ledger: OvertimeLedger
```

- [ ] **Step 6: Change `__init__.py`**

Replace the module body below the imports' `CONFIG_SCHEMA`/`PLATFORMS` lines so it reads (keep `async_setup` as is):

```python
async def async_setup_entry(hass: HomeAssistant, entry: TimebutlerConfigEntry) -> bool:
    """Set up Timebutler from a config entry."""
    client = TimebutlerClient(async_get_clientsession(hass), entry.data[CONF_API_TOKEN])
    ledger = OvertimeLedger(hass, entry.entry_id, entry.options.get(OPT_BALANCE_MINUTES), _as_of(entry))
    await ledger.async_load()
    background = TimebutlerBackgroundCoordinator(hass, entry, client, ledger)
    live = TimebutlerLiveCoordinator(hass, entry, client, background)
    await background.async_config_entry_first_refresh()
    await live.async_config_entry_first_refresh()

    entry.runtime_data = TimebutlerRuntime(live=live, background=background, ledger=ledger)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _as_of(entry: TimebutlerConfigEntry) -> date | None:
    value = entry.options.get(OPT_AS_OF)
    return date.fromisoformat(value) if value else None


async def _async_options_updated(hass: HomeAssistant, entry: TimebutlerConfigEntry) -> None:
    """A new overtime start value: rebuild the ledger."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: TimebutlerConfigEntry) -> bool:
    """Unload a config entry; write the ledger now instead of after the save delay."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.ledger.async_save()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: TimebutlerConfigEntry) -> None:
    """Delete the overtime store of a removed entry."""
    await OvertimeLedger.async_remove(hass, entry.entry_id)
```

Add the imports: `from datetime import date` (before `from homeassistant...`), `from .const import DOMAIN, OPT_AS_OF, OPT_BALANCE_MINUTES` (replacing `from .const import DOMAIN`), and `from .overtime import OvertimeLedger` (after the `.coordinator` import block).

- [ ] **Step 7: Run the tests to verify they pass**

Run: `pytest tests/test_coordinator.py tests/test_init.py -v`
Expected: all PASS. Then `pytest`: no failures.

- [ ] **Step 8: Commit**

```bash
git add custom_components/timebutler/const.py custom_components/timebutler/coordinator.py custom_components/timebutler/__init__.py tests/test_coordinator.py tests/test_init.py
git commit -m "feat: record daily targets and fetch the months the overtime balance needs"
```

---

### Task 4: Options flow for the start value

**Files:**
- Modify: `custom_components/timebutler/config_flow.py`
- Modify: `custom_components/timebutler/strings.json`, `translations/en.json`, `translations/de.json`
- Test: `tests/test_config_flow.py`

**Interfaces:**
- Consumes (Task 1): `parse_balance`, `format_balance`; (Task 3): `OPT_AS_OF`, `OPT_BALANCE_MINUTES`.
- Produces: `TimebutlerOptionsFlow` (step `init`, fields `balance` and `as_of`), stored options `{"balance_minutes": int, "as_of": "YYYY-MM-DD"}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config_flow.py`:

```python
async def _options_entry(hass, options=None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="4711", title="Max Mustermann", data={CONF_API_TOKEN: "tb_test"}, options=options or {}
    )
    entry.add_to_hass(hass)
    return entry


def _suggested(result, key):
    for field in result["data_schema"].schema:
        if field == key:
            return (field.description or {}).get("suggested_value")
    raise AssertionError(f"no field {key}")


async def test_options_flow_stores_balance(hass, frozen_now):
    entry = await _options_entry(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    assert _suggested(result, "as_of") == "2026-10-06"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"balance": "+12:30", "as_of": "2026-10-06"}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {"balance_minutes": 750, "as_of": "2026-10-06"}


async def test_options_flow_prefills_current_values(hass, frozen_now):
    entry = await _options_entry(hass, {"balance_minutes": -105, "as_of": "2026-10-01"})

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert _suggested(result, "balance") == "-1:45"
    assert _suggested(result, "as_of") == "2026-10-01"


@pytest.mark.parametrize(
    ("user_input", "errors"),
    [
        ({"balance": "zwölf", "as_of": "2026-10-06"}, {"balance": "invalid_balance"}),
        ({"balance": "1:00", "as_of": "2026-10-07"}, {"as_of": "as_of_not_past"}),
        ({"balance": "x", "as_of": "2026-10-08"}, {"balance": "invalid_balance", "as_of": "as_of_not_past"}),
    ],
)
async def test_options_flow_rejects_bad_input(hass, frozen_now, user_input, errors):
    entry = await _options_entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)

    result = await hass.config_entries.options.async_configure(result["flow_id"], user_input)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == errors
    assert _suggested(result, "balance") == user_input["balance"]
    assert entry.options == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_config_flow.py -v`
Expected: the new tests FAIL (`UnknownHandler` / options flow not supported); the existing ones PASS.

- [ ] **Step 3: Implement the options flow**

In `custom_components/timebutler/config_flow.py`:

Change the imports to:

```python
from collections.abc import Mapping
from datetime import date, timedelta
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_API_TOKEN
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    DateSelector,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util import dt as dt_util

from .api import TimebutlerAuthError, TimebutlerClient, TimebutlerConnectionError
from .const import DOMAIN, OPT_AS_OF, OPT_BALANCE_MINUTES
from .model import format_balance, parse_balance
```

After `TOKEN_SCHEMA`, add:

```python
CONF_BALANCE = "balance"

OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_BALANCE): TextSelector(),
        vol.Required(OPT_AS_OF): DateSelector(),
    }
)
```

Inside `TimebutlerConfigFlow`, after `VERSION = 1`, add:

```python
    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> TimebutlerOptionsFlow:
        """Options: the overtime balance start value."""
        return TimebutlerOptionsFlow()
```

At the end of the file, add:

```python
class TimebutlerOptionsFlow(OptionsFlow):
    """Set the overtime balance and the day it is valid for."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for balance and date; prefill the current values."""
        errors: dict[str, str] = {}
        if user_input is not None:
            minutes = 0
            try:
                minutes = parse_balance(user_input[CONF_BALANCE])
            except ValueError:
                errors[CONF_BALANCE] = "invalid_balance"
            as_of = date.fromisoformat(user_input[OPT_AS_OF])
            if as_of >= dt_util.now().date():
                errors[OPT_AS_OF] = "as_of_not_past"
            if not errors:
                return self.async_create_entry(
                    data={OPT_BALANCE_MINUTES: minutes, OPT_AS_OF: as_of.isoformat()}
                )
            suggested = user_input
        else:
            options = self.config_entry.options
            yesterday = dt_util.now().date() - timedelta(days=1)
            suggested = {OPT_AS_OF: options.get(OPT_AS_OF, yesterday.isoformat())}
            if OPT_BALANCE_MINUTES in options:
                suggested[CONF_BALANCE] = format_balance(options[OPT_BALANCE_MINUTES])
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(OPTIONS_SCHEMA, suggested),
            errors=errors,
        )
```

- [ ] **Step 4: Add the translations**

In `custom_components/timebutler/strings.json` **and** `custom_components/timebutler/translations/en.json`, add this top-level key directly after the `"config"` object (keep the files identical):

```json
  "options": {
    "step": {
      "init": {
        "title": "Overtime balance",
        "description": "Enter your working-time account balance as the Timebutler web app shows it, and the day it is valid for (end of that day). From the next day on, the integration adds worked minus target time for every finished day. Best use yesterday's balance: days before Home Assistant first saw the daily target are estimated.",
        "data": {
          "balance": "Balance",
          "as_of": "Valid at the end of"
        },
        "data_description": {
          "balance": "Hours:minutes or decimal hours, e.g. +12:30, -1:45 or 12,5"
        }
      }
    },
    "error": {
      "invalid_balance": "Enter the balance as +H:MM, -H:MM or decimal hours (12,5).",
      "as_of_not_past": "The date must be before today."
    }
  },
```

In `custom_components/timebutler/translations/de.json`, at the same place:

```json
  "options": {
    "step": {
      "init": {
        "title": "Überstunden-Saldo",
        "description": "Trag den Stand deines Arbeitszeitkontos so ein, wie ihn die Timebutler-Weboberfläche zeigt, und den Tag, für dessen Ende er gilt. Ab dem Folgetag rechnet die Integration für jeden abgeschlossenen Tag Ist minus Soll dazu. Am besten den Stand von gestern nehmen: Tage, bevor Home Assistant das Tages-Soll zum ersten Mal gesehen hat, werden geschätzt.",
        "data": {
          "balance": "Kontostand",
          "as_of": "Stand am Ende von"
        },
        "data_description": {
          "balance": "Stunden:Minuten oder Dezimalstunden, z. B. +12:30, -1:45 oder 12,5"
        }
      }
    },
    "error": {
      "invalid_balance": "Kontostand als +H:MM, -H:MM oder Dezimalstunden (12,5) eingeben.",
      "as_of_not_past": "Das Datum muss vor heute liegen."
    }
  },
```

Check that all three files are valid JSON:

```bash
python -c "import json; [json.load(open(p, encoding='utf-8')) for p in ('custom_components/timebutler/strings.json', 'custom_components/timebutler/translations/en.json', 'custom_components/timebutler/translations/de.json')]; print('ok')"
```

Expected: `ok`. Also `cmp custom_components/timebutler/strings.json custom_components/timebutler/translations/en.json` prints nothing.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_config_flow.py -v`
Expected: all PASS. Then `pytest`: no failures.

- [ ] **Step 6: Commit**

```bash
git add custom_components/timebutler/config_flow.py custom_components/timebutler/strings.json custom_components/timebutler/translations tests/test_config_flow.py
git commit -m "feat: add an options flow for the overtime balance start value"
```

---

### Task 5: Sensor, README and version 0.3.0

**Files:**
- Modify: `custom_components/timebutler/sensor.py`
- Modify: `custom_components/timebutler/strings.json`, `translations/en.json`, `translations/de.json`
- Modify: `custom_components/timebutler/const.py` (`VERSION`), `custom_components/timebutler/manifest.json` (`version`)
- Modify: `README.md`
- Test: `tests/test_overtime_sensor.py` (new)

**Interfaces:**
- Consumes (Task 2): `OvertimeLedger.balance`, `.today_difference`, `.as_of`; (Task 3): `TimebutlerRuntime.ledger`, `BackgroundData.entries`, `.absences`, `TodaySummary.worked`, `.commitment`.
- Produces: entity `sensor.timebutler_ueberstunden`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_overtime_sensor.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_overtime_sensor.py -v`
Expected: FAIL with `AttributeError: 'NoneType' object has no attribute 'state'` (the entity does not exist).

- [ ] **Step 3: Implement the sensor**

In `custom_components/timebutler/sensor.py`:

Extend the sensor component import with `SensorStateClass`:

```python
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
```

After `_next_absence_attrs`, add:

```python
def _overtime(rt: TimebutlerRuntime) -> tuple[int, list[str]] | None:
    background = rt.background.data
    if background is None:
        return None
    return rt.ledger.balance(dt_util.now().date(), background.entries, background.absences)


def _overtime_value(rt: TimebutlerRuntime) -> int | None:
    result = _overtime(rt)
    return result[0] if result else None


def _overtime_attrs(rt: TimebutlerRuntime) -> dict[str, Any] | None:
    result = _overtime(rt)
    if result is None or rt.background.data is None or rt.ledger.as_of is None:
        return None
    balance, estimated = result
    today = _today(rt)
    with_today = None
    if today is not None:
        with_today = balance + rt.ledger.today_difference(
            dt_util.now().date(), today.worked, today.commitment, rt.background.data.absences
        )
    return {"mit_heute": with_today, "stand_vom": rt.ledger.as_of.isoformat(), "geschaetzt": estimated}
```

Append to the `SENSORS` tuple (after `naechste_abwesenheit`):

```python
    TimebutlerSensorDescription(
        key="ueberstunden",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        sources=("background", "live"),
        value_fn=_overtime_value,
        attrs_fn=_overtime_attrs,
    ),
```

Change `_unrecorded_attributes` in `TimebutlerSensor` to:

```python
    # Day maps and today's live balance change every minute while clocked in;
    # keep them out of history.
    _unrecorded_attributes = frozenset({"tage", "liste", "mit_heute", "geschaetzt"})
```

- [ ] **Step 4: Add the sensor name translations**

In `entity.sensor` of `strings.json` and `translations/en.json` (identical), after `"naechste_abwesenheit"`:

```json
      "ueberstunden": {
        "name": "Overtime"
      }
```

In `translations/de.json`, same place:

```json
      "ueberstunden": {
        "name": "Überstunden"
      }
```

Run the JSON check and the `cmp` from Task 4 Step 4 again; expected `ok` and no `cmp` output.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_overtime_sensor.py -v`
Expected: all PASS.

- [ ] **Step 6: Version 0.3.0 and README**

- `custom_components/timebutler/const.py`: `VERSION = "0.3.0"`.
- `custom_components/timebutler/manifest.json`: `"version": "0.3.0"`.
- `README.md`, first paragraph: after "vacation balance and upcoming absences" insert ", an overtime balance kept from a start value you enter". In the entity table, after the `sensor.timebutler_naechste_abwesenheit` row, add:

```markdown
| `sensor.timebutler_ueberstunden` | Overtime balance in minutes up to the end of yesterday (negative = minus hours); unknown until a start value is set. Attributes `mit_heute` (balance if you stopped now), `stand_vom` (start date), `geschaetzt` (days whose target was estimated) |
```

- `README.md`: add a section before `## Development`:

```markdown
## Overtime balance

Timebutler's API has no endpoint for the working-time account, so the integration keeps the balance itself:

1. In Home Assistant open Settings → Devices & services → Timebutler → Configure. Enter the balance the Timebutler web app shows (`+12:30`, `-1:45` or `12,5` hours) and the day it is valid for (default: yesterday).
2. From the next day on, every finished day adds worked minus target time. The target is the one Timebutler reported for that day (recorded every minute while Home Assistant runs), so public holidays and part-time days come from Timebutler.
3. Approved absences: a full day counts as neutral, a half day halves the target, `AUA` (overtime reduction) keeps the full target, `UEB` (overtime worked) adds its hours. Pending and rejected requests are ignored.

Limits: a day Home Assistant was down for entirely uses the target of the last same weekday (listed in `geschaetzt`; a public holiday on such a day counts as a working day). Corrections in Timebutler older than the previous month are not picked up. When the value drifts from the web app, enter the current balance again in the options.
```

- [ ] **Step 7: Run the full suite**

Run: `pytest`
Expected: no failures (188 baseline plus the new tests).

- [ ] **Step 8: Commit**

```bash
git add custom_components/timebutler/sensor.py custom_components/timebutler/strings.json custom_components/timebutler/translations custom_components/timebutler/const.py custom_components/timebutler/manifest.json README.md tests/test_overtime_sensor.py
git commit -m "feat: add the overtime balance sensor, bump to 0.3.0"
```
