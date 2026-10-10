# Timebutler for Home Assistant

Custom integration that shows your own [Timebutler](https://timebutler.com) data in Home Assistant and lets you act on it: time clock state and controls, worked time today/this week/this month, vacation balance and upcoming absences, plus services to request or delete absences and to add or delete time entries. Uses the Timebutler API v2 with a personal access token.

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
| `sensor.timebutler_naechste_abwesenheit` | Start date of the next approved or pending absence; attributes `id`, `art`, `bis`, `tage`, `status`, `halbtags`, `liste` (next 5, each with `id`) |

The clock and today's values refresh every minute, everything else every 15 minutes and right after you clock out.

## Buttons

| Entity | Action | Usable while |
|---|---|---|
| `button.timebutler_start` | Start the clock | off |
| `button.timebutler_pause` | Pause | running |
| `button.timebutler_fortsetzen` | Resume | paused |
| `button.timebutler_stopp` | Stop and save | running, paused, waiting |
| `button.timebutler_stopp_pausenkorrektur` | Stop and accept a break-regulation correction | running, paused |
| `button.timebutler_verwerfen` | Discard the session (disabled by default) | not off |
| `button.timebutler_dienstreise_start` / `_ende` | Business trip start/end (disabled by default) | running or paused |

A button is *unavailable* while the clock state does not allow its action. [`dashboards/clock-buttons.yaml`](dashboards/clock-buttons.yaml) is a card that shows only the usable ones.

## Services

All services take an optional `config_entry_id`; it is only needed when several Timebutler accounts are set up. Errors (e.g. "terminal only", "clock already running") are shown as translated messages.

| Service | Fields | Response |
|---|---|---|
| `timebutler.start`, `.pause`, `.resume`, `.cancel`, `.business_trip_start`, `.business_trip_end` | – | – |
| `timebutler.stop` | `project`, `category` (name or ID; default: your Timebutler defaults), `remarks`, `break_regulation` (`apply`/`ignore`) | `outcome`, `id`, `gearbeitet`, `pause` |
| `timebutler.request_absence` | `type` (code like `URL` or name like `Urlaub`), `start_date`, `end_date`, `half_day` (`morning`/`afternoon`), `hours` (only `UEB`/`AUA`), `comments`, `substitute` (name or ID) | `id`, `status`, `tage` |
| `timebutler.delete_absence` | `date` (a day of the absence) or `id`, `reason` | `id`, `result` (`deleted` or `cancellation_requested`) |
| `timebutler.add_time_entry` | `date`, `start_time`, `end_time` (same day, HA time zone), `break_minutes`, `project`, `category`, `remarks` | `id`, `gearbeitet`, `pause` |
| `timebutler.delete_time_entry` | `date` (+ `start_time` when the day has several entries) or `id` | `id`, `result` (`deleted` or `deletion_requested`) |

### Break regulation

When a stop violates your company's break regulation, Timebutler does not save the entry. The integration then keeps the clock running, shows the required change as an error and as a persistent notification, and fires the event `timebutler_break_regulation` (`detail`, `obligatory`, `has_no_remedy`, `correction_work_minutes`, `correction_break_minutes`, `resulting_work_minutes`, `resulting_break_minutes`). Finish with *Stop with break correction* or `timebutler.stop` with `break_regulation: apply` (`ignore` saves unchanged and is refused for obligatory regulations).

### Example: clock out when leaving home

```yaml
automation:
  - alias: Ausstempeln beim Verlassen
    triggers:
      - trigger: state
        entity_id: person.me
        from: home
    conditions:
      - condition: state
        entity_id: binary_sensor.timebutler_eingestempelt
        state: "on"
    actions:
      - action: timebutler.stop
        continue_on_error: true
```

A stop that needs a break-regulation decision raises an error and fires the `timebutler_break_regulation` event instead.

### Example: react to the break regulation

```yaml
automation:
  - alias: Pausenregelung melden
    triggers:
      - trigger: event
        event_type: timebutler_break_regulation
    actions:
      - action: notify.notify
        data:
          message: "Timebutler: {{ trigger.event.data.detail }}"
```

## Development

See [tests/README.md](tests/README.md). `scripts/live_check.py` queries the real API with `TIMEBUTLER_TOKEN` from the environment (never commit its output).
