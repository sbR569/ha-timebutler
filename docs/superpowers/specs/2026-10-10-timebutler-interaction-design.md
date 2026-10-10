# Timebutler interaction – design

Date: 2026-10-10 · Status: approved in conversation, pending spec review

## Goal

The integration is read-only today. It shall also act on Timebutler for the token's own user:

1. **Time clock** – start, pause, resume, stop, cancel, business trip start/end; usable from the touch panel (buttons) and from automations (buttons and services).
2. **Absences** – request and delete/cancel absence requests.
3. **Time entries** – add and delete completed time entries.

Implemented in three phases in that order; each phase ships working and tested on its own.

Out of scope: editing absence requests (`PUT /absence-requests/{id}` only works for pending requests, where delete + new request is equivalent), manager actions (approve/reject), OAuth.

## API facts (Timebutler API v2, OpenAPI spec `app.timebutler.com/api/v2/openapi.json`)

| Call | Body | Answer | Notable errors |
|---|---|---|---|
| `POST /time-clock/start` | – | `TimeClockStatusDto` | 403 terminal only, 409 already running / start conditions |
| `POST /time-clock/pause` | – | status | 409 not running |
| `POST /time-clock/resume` | – | status | 409 not paused |
| `POST /time-clock/stop` | `projectId`, `categoryId`, `remarks`, `breakRegulation` (`apply`/`ignore`) | `TimeClockStopResponse` (`outcome` `saved`/`confirmationRequired`, `id`, `workedMinutes`, `breakMinutes`, `breakRegulation{obligatory, hasNoRemedy, detailText, …}`); 204 when a `waiting` clock was discarded | 400 bad project/category, 409 too short / duplicate / not active |
| `POST /time-clock/cancel` | – | status (`idle`) | 409 already idle |
| `POST /time-clock/business-trip-start` / `-end` | – | status | 400 business trips disabled, 409 wrong state |
| `GET /projects` | – | `projects[{id,name}]`, `defaultProjectId`, `isProjectMandatory` | |
| `GET /categories` | – | `categories[{id,name}]`, `defaultCategoryId`, `isCategoryMandatory` | |
| `GET /substitute-employees` | – | `[{id,name,department}]` | |
| `POST /absence-requests` | `absenceTypeId`, `startDate`, `endDate`, `halfDay`, `comments`, `hours`, `substituteEmployeeId` | `AbsenceRequestDto` | 400 invalid fields, 403 type not allowed, 409 overlap |
| `DELETE /absence-requests/{id}` | `reason` | `{id, result: deleted \| cancellation_requested}` | 400 reason required, 403, 404 |
| `POST /time-tracking/clock-entry` | `startTimestamp`, `endTimestamp` (ms, same day), `pauseSeconds`, `projectId`, `categoryId`, `remarks` | `{id, workedMinutes, breakMinutes}` | 400, 403, 422 remarks too long |
| `DELETE /time-entries/{id}` | – | `{id, result: deleted \| deletion_requested}` | 403, 404, 409 month closed |

All time-clock and time-tracking calls answer 400 when time tracking or the time clock is disabled.

## Architecture

Approach: a separate service layer. Coordinators stay pure pollers.

| Unit | Responsibility |
|---|---|
| `api.py` | HTTP: generic `_request(method, path, params, json)`; read methods unchanged; new write methods. |
| `model.py` | Pure functions (no HA imports): resolve names/IDs, validate inputs, match dates to absences/entries, convert local date+time to ms timestamps. |
| `actions.py` | Executes one action for a config entry: calls the client, maps client errors to translated `HomeAssistantError`/`ServiceValidationError`, pushes results into the coordinators, handles the break-regulation confirmation. Used by buttons and services. |
| `button.py` | Time clock buttons. |
| `services.py` + `services.yaml` | Service registration in `async_setup`, schemas, config entry selection, response data. |

### Client errors

`_get` becomes `_request`. Status mapping:

| Status | Read call | Write call |
|---|---|---|
| 401 | `TimebutlerAuthError` | `TimebutlerAuthError` |
| 403 | `TimebutlerAuthError` (as today) | `TimebutlerForbiddenError` – must **not** start reauth (e.g. "terminal only") |
| 400 | `TimebutlerTimeTrackingDisabled` where flagged, else connection error (as today) | `TimebutlerBadRequestError` |
| 404 | connection error | `TimebutlerNotFoundError` |
| 409 | connection error | `TimebutlerConflictError` |
| 422 | connection error | `TimebutlerBadRequestError` |
| other ≥ 400, network, timeout, bad JSON | `TimebutlerConnectionError` | `TimebutlerConnectionError` |

204 returns `None`. The new error classes derive from `TimebutlerError`; the server's response text (first 300 chars) is kept on the exception for logging, not shown verbatim to users.

`actions.py` maps them: `TimebutlerAuthError` → `ConfigEntryAuthFailed` is not raised from a service; instead the entry starts reauth (`entry.async_start_reauth`) and a `HomeAssistantError` is raised. Forbidden / conflict / bad request / not found → `HomeAssistantError` with a translation key specific to the action (e.g. `clock_already_running`, `terminal_only`, `absence_overlap`, `month_closed`). Connection errors → `HomeAssistantError` `connection_error`.

### Project and category resolution

`project` and `category` accept a name (case-insensitive, exact match) or an ID. Lists are fetched per call via `GET /projects` / `GET /categories` (only when the action needs them). When the field is omitted, the company's `defaultProjectId` / `defaultCategoryId` is sent if set, else `0`. Unknown name → `ServiceValidationError` listing the available names.

## Phase 1 – time clock

### Buttons

Fixed entity IDs as for the existing entities (`button.timebutler_<key>`), translated names, device "Timebutler". Fed by the live coordinator. A button is available only while the live coordinator is up, time tracking is on and the clock state allows the action:

| Key / entity | Action | Available while | Enabled by default |
|---|---|---|---|
| `start` | start | `idle` | yes |
| `pause` | pause | `running` | yes |
| `fortsetzen` | resume | `paused` | yes |
| `stopp` | stop, no break decision | `running`, `paused`, `waiting` | yes |
| `stopp_pausenkorrektur` | stop with `breakRegulation: apply` | `running`, `paused` | yes |
| `verwerfen` | cancel | not `idle` | no |
| `dienstreise_start` | business trip start | `running`/`paused` and no trip | no |
| `dienstreise_ende` | business trip end | `running`/`paused` and trip active | no |

### Services

| Service | Fields |
|---|---|
| `timebutler.start`, `timebutler.pause`, `timebutler.resume`, `timebutler.cancel`, `timebutler.business_trip_start`, `timebutler.business_trip_end` | `config_entry_id` (optional) |
| `timebutler.stop` | `config_entry_id`, `project`, `category`, `remarks`, `break_regulation` (`apply`/`ignore`), all optional. Response (optional): `outcome`, `id`, `gearbeitet`, `pause` |

`config_entry_id` (config entry selector, integration `timebutler`) is optional; omitted it selects the only loaded entry, otherwise `ServiceValidationError` asks for it. Services are registered once in `async_setup` and fail with a validation error when the selected entry is not loaded.

Buttons and the field-less services call the same `actions.py` functions as `timebutler.stop`.

### After a clock action

- The returned `TimeClockStatusDto` is parsed and pushed into the live coordinator immediately (`async_set_updated_data` with the new clock and the current today summary), so buttons and the clock sensor flip without waiting for the next poll.
- Then `live.async_request_refresh()` so today's minutes follow.
- After a saved stop or a cancel the existing clocked-in → out transition triggers the background refresh (week/month), as today.

### Break regulation

When `stop` answers `outcome: confirmationRequired`:

1. Nothing is saved; the clock keeps running.
2. A persistent notification (id `timebutler_break_regulation_<entry_id>`) shows `detailText` and how to finish: press "Stopp mit Pausenkorrektur" or call `timebutler.stop` with `break_regulation: apply` (or `ignore`, mentioned only when not `obligatory`; when `hasNoRemedy`, the text says the session can only be saved unchanged (if allowed) or discarded).
3. Event `timebutler_break_regulation` fires with `config_entry_id`, `detail`, `obligatory`, `has_no_remedy`, `correction_work_minutes`, `correction_break_minutes`, `resulting_work_minutes`, `resulting_break_minutes`.
4. The action raises `HomeAssistantError` (translation `break_regulation_confirmation`) carrying `detailText`, so the UI shows a toast.

The notification is dismissed after any successful stop or cancel of that entry. Sending `ignore` for an obligatory regulation yields `confirmationRequired` again and goes through the same path.

A 204 (waiting clock discarded) counts as success with `outcome: discarded`.

## Phase 2 – absences

### `timebutler.request_absence`

Fields: `config_entry_id`, `type` (required; absence type code such as `URL` or its name such as "Urlaub", resolved via the background coordinator's absence types, case-insensitive), `start_date` (required), `end_date` (optional, defaults to `start_date`), `half_day` (`morning`/`afternoon`), `hours` (number), `comments`, `substitute` (name or ID, resolved via `GET /substitute-employees`).

Local validation (`ServiceValidationError`, translated) before calling the API:

- `end_date` ≥ `start_date`
- `half_day` set → single day
- type `UEB` or `AUA` → `hours` required and > 0, single day; other types → `hours` not allowed
- unknown type → error listing known types; unknown substitute → error listing names

`hours` is sent as a string with a dot decimal separator.

Response (optional): `id`, `status`, `tage`. 409 → `absence_overlap`, 403 → `absence_type_forbidden`.

### `timebutler.delete_absence`

Fields: `config_entry_id`, exactly one of `id` / `date`, optional `reason`.

`date` matches the user's own requests with status `pending` or `approved` whose `startDate ≤ date ≤ endDate`, taken from a fresh `GET /absence-requests/my`. No match → `ServiceValidationError` `absence_not_found`; several matches (e.g. two half days) → `absence_ambiguous` listing the IDs with half-day info.

Response (optional): `id`, `result` (`deleted`/`cancellation_requested`). 400 → `absence_reason_required`, 403 → `absence_delete_forbidden`, 404 → `absence_not_found`.

### Visibility

`sensor.timebutler_naechste_abwesenheit` gets attribute `id`, and every item in `liste` gets `id`. (`liste` is already excluded from the recorder.)

### After an absence action

`background.async_request_refresh()`.

## Phase 3 – time entries

### `timebutler.add_time_entry`

Fields: `config_entry_id`, `date` (required), `start_time` and `end_time` (required, time selector), `break_minutes` (optional, default 0), `project`, `category`, `remarks`.

`date` + times are interpreted in Home Assistant's time zone and converted to Unix ms (`model.local_timestamp_ms`, DST-safe via `dt_util` zone). Validation: `end_time` > `start_time` (no entries across midnight; the API requires the same day), `break_minutes` ≥ 0 and smaller than the duration.

Response (optional): `id`, `gearbeitet`, `pause`. 403 → `time_entry_forbidden`, 422 → `remarks_too_long`.

### `timebutler.delete_time_entry`

Fields: `config_entry_id`, exactly one of `id` / `date`, optional `start_time` (only with `date`).

`date` fetches that month's entries via `GET /time-entries/my`; match by `date` and, if given, `startTime` (`HH:MM`). Exactly one entry on that day → it; none → `time_entry_not_found`; several without `start_time` → `time_entry_ambiguous` listing start times.

Response (optional): `id`, `result` (`deleted`/`deletion_requested`). 409 → `month_closed`, 403 → `time_entry_delete_forbidden`, 404 → `time_entry_not_found`.

### After a time entry action

`live.async_request_refresh()` and `background.async_request_refresh()`.

## Translations

`strings.json`, `translations/de.json`, `translations/en.json` gain: button entity names, service names/descriptions/field descriptions, and `exceptions` entries for every translation key above. German wording follows the existing entity names (e.g. "Stempeln ist für dich nur am Terminal erlaubt", "Die Uhr läuft bereits").

## Testing

Existing setup: pytest + `pytest-homeassistant-custom-component`, synthetic fixtures following the OpenAPI spec, HTTP mocked via `aioclient_mock`.

- `test_api.py`: method, path and JSON body of every write call; status mapping table above, especially 403 on a write call → `TimebutlerForbiddenError` (no reauth), 204 → `None`.
- `test_model.py`: name/ID resolution (project, category, absence type, substitute; defaults; unknown names), absence validation rules, date → absence matching (none, one, several), date/time → entry matching, local time → ms including a DST change day.
- `test_button.py`: availability per clock state, default-disabled buttons, press → API call → state updated immediately, error → `HomeAssistantError` with the right translation key.
- `test_services.py`: config entry selection (one entry, several, not loaded), response data, break-regulation path (error + persistent notification + event; notification dismissed after a successful stop), refresh calls after actions, each error mapping.
- New fixtures: `stop_saved.json`, `stop_confirmation.json`, `projects.json`, `categories.json`, `substitutes.json`, `absence_created.json`, `delete_result.json`, `clock_entry.json`.

## Docs and housekeeping

- README: drop "Read-only", add sections *Buttons* and *Services* with automation examples (clock out when leaving home; reacting to `timebutler_break_regulation`).
- `dashboards/`: the existing `.jinja` files are text templates and stay unchanged; a new `dashboards/clock-buttons.yaml` holds a card (horizontal stack of the four main buttons, each hidden via `visibility` when unavailable) to place next to them. The README references it.
- `scripts/live_check.py` stays read-only.
- `const.VERSION` and `manifest.json` version → `0.2.0`.
