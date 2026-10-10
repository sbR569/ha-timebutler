# Timebutler for Home Assistant

Custom integration that shows your own [Timebutler](https://timebutler.com) data in Home Assistant: time clock state, worked time today/this week/this month, vacation balance and upcoming absences. Read-only, uses the Timebutler API v2 with a personal access token.

## Installation (HACS)

1. HACS → ⋮ → *Custom repositories* → add `https://github.com/sbR569/ha-timebutler`, type *Integration*.
2. Install *Timebutler*, restart Home Assistant.
3. *Settings → Devices & services → Add integration → Timebutler*, paste your personal access token (starts with `tb_`).

When Timebutler rejects the token later, Home Assistant asks for a new one (reauth).

## Entities

| Entity | Meaning |
|---|---|
| `sensor.timebutler_stempeluhr` | Clock state `idle`/`running`/`paused`/`waiting`; attributes `seit` (start), `dienstreise` |
| `binary_sensor.timebutler_eingestempelt` | On while the clock runs or is paused |
| `sensor.timebutler_arbeitszeit_heute` | Minutes worked today, including the running session |
| `sensor.timebutler_soll_heute` | Target minutes today |
| `sensor.timebutler_rest_heute` | Remaining minutes (negative = overtime) |
| `sensor.timebutler_fortschritt_heute` | Percent of today's target |
| `sensor.timebutler_pause_heute` | Break minutes today |
| `sensor.timebutler_arbeitszeit_woche` | Minutes this week; attribute `tage` (minutes per day, Mon–Sun) |
| `sensor.timebutler_arbeitszeit_monat` | Minutes this month; attributes `tage`, `arbeitstage` |
| `sensor.timebutler_urlaub_rest` | Vacation days left; attributes `gesamt`, `genehmigt`, `beantragt` |
| `sensor.timebutler_krankheitstage` | Sick days this year |
| `sensor.timebutler_naechste_abwesenheit` | Start date of the next approved or pending absence; attributes `art`, `bis`, `tage`, `status`, `halbtags`, `liste` (next 5) |

The clock and today's values refresh every minute, everything else every 15 minutes and right after you clock out.

## Development

See [tests/README.md](tests/README.md). `scripts/live_check.py` queries the real API with `TIMEBUTLER_TOKEN` from the environment (never commit its output).
