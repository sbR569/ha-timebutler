# Overtime balance (Überstunden-Saldo) – design

Date: 2026-10-15 · Status: approved in conversation, pending spec review

## Goal

Show the user's working-time account balance ("Arbeitszeitkonto", overtime) in Home Assistant as
`sensor.timebutler_ueberstunden`.

Timebutler API v2 has no endpoint for it: the OpenAPI spec (41 operations) has no balance, carry-over or
account field anywhere. `/time-tracking/work-summary` gives target/worked/remaining for **today only** and
takes no parameters; `/time-entries/my` gives worked minutes per entry and day; absence requests of the types
`UEB` (overtime worked) and `AUA` (overtime reduction) carry `hours`. API v1 has no such endpoint either.

So the integration keeps the balance itself: the user enters the current balance once (read from the Timebutler
web UI), and from then on the integration adds worked minus target for every finished day.

Out of scope: showing the balance on the touch panel (separate follow-up in `ha-touch-panel`), reconstructing
days before the start date, editing the balance from a service.

## Decisions from the conversation

| Question | Decision |
|---|---|
| Start date | "From now": the entered balance is valid at the end of a given day (default yesterday). No history before it is reconstructed. |
| Today in the value | The state is the balance **up to yesterday**; it changes once a day. Today's live value is an attribute. |
| Where the start value is entered | Integration options (Timebutler → Configure). |
| Approach | Inside the `ha-timebutler` integration (not a template/automation package in `ha-touch-panel`), so later corrections in Timebutler, absences and UEB hours are taken into account and a day without HA is not lost. |

## Observed data (live, 2026-10-15)

- `sensor.timebutler_soll_heute`: 480 on weekdays (also on 2026-10-12, a day with only 240 worked minutes), 0 on
  Saturday/Sunday. Changes at midnight.
- It is not known yet whether Timebutler's daily target is already reduced on holidays, vacation, half days or
  `AUA` days. The rules below give the right result in both cases.

## Calculation

### Terms

- **Start**: `balance_minutes` (int, may be negative) valid at the end of `as_of` (a date before today).
- **Closed day**: every day `d` with `as_of < d < today` (local time).
- **Worked(d)**: sum of `workedMinutes` of the time entries on `d`, without `rejected` ones (same rule as the
  existing week/month sensors, `_entry_totals`).
- **Recorded target(d)**: the last `commitmentMinutes` the live coordinator saw while `d` was today.
- **Weekday norm(d)**: the recorded target of the most recent earlier day with the same weekday that has a
  recorded target and no approved absence. None if there is no such day.
- **Raw target(d)**: recorded target(d); if missing (HA was down all day), weekday norm(d); if that is missing
  too, 0. A day whose raw target did not come from a recording is **estimated**.
- **Absences**: only `approved` absence requests count; a request covers every date from `startDate` to
  `endDate`. `halfDay` set means half a day.

### Effective target and day difference

For a day `d` with raw target `r` and norm `n` (use `r` when `n` is None):

1. **Ordinary absences** (any type except `UEB`/`AUA`) on `d`: fraction `f` = 1 per full-day request,
   0.5 per half-day request, capped at 1.
   Effective target = `min(r, round(n × (1 − f)))`. A full day becomes 0 (neutral); a half day becomes at most
   half the norm, whether or not Timebutler already halved it.
2. Otherwise, **`AUA`** on `d`: effective target = `max(r, n)`. The reduction is paid from the account, so the
   target must stay, even if Timebutler lowered it.
3. Otherwise: effective target = `r`.

Day difference(d) = worked(d) − effective target(d) + 60 × (sum of `hours` of approved `UEB` requests on `d`),
rounded to whole minutes.

Balance = `balance_minutes` + frozen sum (see below) + Σ day difference over the closed days that are not frozen.

**Today** (`mit_heute` attribute): the balance plus today's difference, computed with the same rules, with
today's live worked minutes (`TodaySummary.worked`, which already includes a running clock session) and the
live target as recorded target.

### Accepted inaccuracies

- A day HA was down for entirely uses the weekday norm; a public holiday on such a day counts as a working day.
  These days are listed in the `geschaetzt` attribute; the user re-syncs via the options if needed.
- Right after midnight, yesterday's last session may not be in the time entries yet. The clock-out already
  triggers a background refresh, so this only matters for sessions still running at midnight. They reach the
  balance with the next background update (≤ 15 min).
- Corrections in Timebutler older than the previous month are not picked up (frozen, see below).

## Architecture

| Unit | Change |
|---|---|
| `model.py` | New pure functions: `parse_balance(text) -> int` (minutes from `+12:30`, `-1:45`, `12,5`, `12.5`, `-0:30`), `day_target(...)`, `day_difference(...)`, `overtime_balance(...)` returning total and estimated days. No HA imports. |
| `overtime.py` (new) | `OvertimeLedger`: wraps a `homeassistant.helpers.storage.Store` per config entry. Holds recorded targets and the frozen sum; methods `async_load`, `record_target(day, minutes)` (saved with `async_delay_save`), `freeze(...)`, `reset(as_of)`. |
| `coordinator.py` | Live coordinator records today's target into the ledger after each successful update. Background coordinator fetches the months the balance needs (below), then lets the ledger freeze old days. `TimebutlerRuntime` gets a `ledger` field. |
| `config_flow.py` | New options flow: `Kontostand` (text) and `Stand vom` (date). |
| `__init__.py` | Create and load the ledger before the first refresh; reload the entry when the options change; `async_remove_entry` removes the store file. |
| `sensor.py` | New sensor `ueberstunden`. |
| `strings.json`, `translations/{de,en}.json` | Options form, errors, sensor name and attribute names. |
| `const.py`, `manifest.json` | Version 0.3.0. |
| `README.md` | Entity table row and a section "Überstunden-Saldo" (setup, rules, limits). |

### Options flow

- `balance` (text, required): parsed with `parse_balance`; unparsable → form error `invalid_balance`.
- `as_of` (date selector, required, default yesterday): must be before today, else form error `as_of_not_past`.
- Stored in `entry.options` as `balance_minutes` (int) and `as_of` (ISO date). The form is prefilled with the
  current values (balance formatted as `+H:MM`).
- Saving reloads the entry. When `as_of` or `balance_minutes` changed, the ledger resets its frozen sum and
  freeze date (recorded targets stay, they are still valid).

### Storage (`.storage/timebutler.<entry_id>.overtime`)

```json
{
  "version": 1,
  "data": {
    "targets": {"2026-10-15": 480, "2026-10-16": 480},
    "frozen_through": "2026-08-31",
    "frozen_minutes": -135,
    "as_of": "2026-10-14"
  }
}
```

- `targets`: recorded target per day. Entries older than `frozen_through` are dropped except the last one per
  weekday that had no absence (the weekday norm needs them).
- `frozen_through` / `frozen_minutes`: days after `as_of` up to and including `frozen_through` are already summed
  up and are no longer fetched. `as_of` lets the ledger notice an options change and reset.

### Fetching and freezing

- Background months = `months_needed(today)` (as today) ∪ every month from the month of
  `max(as_of, frozen_through) + 1 day` up to the current month. Without a start value nothing extra is fetched.
- After a successful background update, all closed days before the first day of the previous month that are
  not frozen yet are added to `frozen_minutes`, and `frozen_through` moves to the last day of the month before
  the previous month. As a result, the month that just left the window is fetched once more at the month
  change, then frozen. In steady state the background fetches the same one or two months as today.
- Absences come from `/absence-requests/my`, which the background already fetches every 15 minutes.

### Sensor `sensor.timebutler_ueberstunden`

- Name "Überstunden" / "Overtime", device class duration, unit min, `state_class: measurement` (long-term
  statistics for a history graph), display precision 0.
- Sources: background (primary, availability) and live.
- State: balance up to yesterday; `None` (unknown) when no start value is set, or before both coordinators
  have data.
- Attributes: `mit_heute` (int minutes), `stand_vom` (ISO date `as_of`), `geschaetzt` (list of ISO dates, empty
  normally). `geschaetzt` is unrecorded (`_unrecorded_attributes`).
- Since entity states are written on every live update (every minute), the day boundary needs no extra timer:
  the first update after midnight moves yesterday into the state.

## Error handling

- Background or live update fails: as today, the coordinator marks itself failed; the sensor follows the
  background coordinator's availability. Targets are only recorded from successful live updates.
- Time tracking disabled (`LiveData.today is None`): no targets are recorded; the balance still adds up recorded
  and estimated days; `mit_heute` is None.
- Corrupt or missing store file: start empty (all targets estimated until recorded again) and log a warning.

## Testing

- `tests/test_model.py`: `parse_balance` (signs, `H:MM`, decimal with comma/point, garbage), `day_target` for
  every rule (none, full day, half day, two half days, half day when Timebutler already halved, `AUA` with
  lowered and unlowered target, `UEB` hours, pending/rejected absences ignored, missing norm), estimated days,
  multi-day absences over a weekend, balance over a month boundary.
- `tests/test_overtime.py` (new): ledger load/save, `record_target` overwrites per day, freezing at a month
  change (two-months-back month frozen once, sum correct), reset on changed `as_of`, pruning keeps weekday norms.
- `tests/test_config_flow.py`: options flow happy path, `invalid_balance`, `as_of_not_past`, prefill.
- `tests/test_coordinator.py`: live update records today's target; background requests the extra months only
  when a start value is set.
- Sensor test: state unknown without options; state, `mit_heute`, `stand_vom` with fixture data; state changes
  across midnight (freezer time).

## Rollout

Branch `claude/ueberstunden-saldo` in `ha-timebutler`, merged to `main`, release `v0.3.0`, updated in HA via HACS
(HA restart, to be agreed with the user). After the update, the user enters the balance from the Timebutler web
UI in the options. Verification: compare the sensor with the web UI after a few days, including one with an
absence.
