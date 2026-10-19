"""Pure data shaping for the Timebutler integration (no Home Assistant imports)."""

from __future__ import annotations

import calendar
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
import math
import re
from typing import Any

from .const import HOLIDAY_REGION

CLOCK_STATES = ("idle", "running", "paused", "waiting")
CLOCKED_IN_STATES = ("running", "paused")
UPCOMING_STATUSES = ("approved", "pending")
OVERTIME_TYPES = ("UEB", "AUA")


@dataclass(frozen=True)
class ClockState:
    """Normalized /time-clock/status answer."""

    status: str | None
    started: datetime | None
    business_trip: bool
    session_minutes: int
    session_break_minutes: int

    @property
    def clocked_in(self) -> bool:
        """True while the clock runs or is paused."""
        return self.status in CLOCKED_IN_STATES


@dataclass(frozen=True)
class TodaySummary:
    """Today's minutes, including a running session where needed."""

    commitment: int
    worked: int
    remaining: int
    percent: int
    break_minutes: int


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def parse_clock(dto: dict[str, Any]) -> ClockState:
    """Normalize the clock status; unknown states become None."""
    status = dto.get("status")
    start_ms = dto.get("startTimestamp")
    started = None
    if isinstance(start_ms, (int, float)) and not isinstance(start_ms, bool):
        started = datetime.fromtimestamp(start_ms / 1000, tz=UTC)
    return ClockState(
        status=status if status in CLOCK_STATES else None,
        started=started,
        business_trip=bool(dto.get("isBusinessTripActive")),
        session_minutes=_int(dto.get("workTimeElapsedSeconds")) // 60,
        session_break_minutes=_int(dto.get("accumulatedBreakSeconds")) // 60,
    )


def _session_today(clock: ClockState, now: datetime) -> int:
    """Minutes of the running session that fall on today."""
    session = clock.session_minutes
    if clock.started is not None and clock.started.astimezone(now.tzinfo).date() < now.date():
        # Session started yesterday: only the part since midnight is today's.
        session = min(session, now.hour * 60 + now.minute)
    return session


def summary_includes_running(
    summary: dict[str, Any],
    clock: ClockState | None,
    entries: list[dict[str, Any]],
    now: datetime,
) -> bool:
    """Whether workedMinutes already counts the running session.

    Timebutler does not document this. While clocked in, the summary either
    equals today's finished entries or those plus the running session. The
    midpoint between the two decides, so a lagging summary or a break that
    Timebutler already deducted does not flip the result.
    """
    if clock is None or not clock.clocked_in:
        return False
    session = _session_today(clock, now)
    if session <= 2:
        return False
    finished = _entry_totals(entries).get(now.date().isoformat(), 0)
    return _int(summary.get("workedMinutes")) >= finished + session / 2


def today_summary(
    summary: dict[str, Any],
    clock: ClockState | None,
    includes_running: bool,
    now: datetime,
) -> TodaySummary:
    """Combine the work summary with the running session."""
    commitment = _int(summary.get("commitmentMinutes"))
    worked = _int(summary.get("workedMinutes"))
    break_minutes = _int(summary.get("breakMinutes"))
    if includes_running:
        return TodaySummary(
            commitment=commitment,
            worked=worked,
            remaining=_int(summary.get("remainingMinutes")),
            percent=_int(summary.get("percentDone")),
            break_minutes=break_minutes,
        )
    if clock is not None and clock.clocked_in:
        worked += _session_today(clock, now)
        break_minutes += clock.session_break_minutes
    percent = math.floor(worked * 100 / commitment + 0.5) if commitment > 0 else 0
    return TodaySummary(
        commitment=commitment,
        worked=worked,
        remaining=commitment - worked,
        percent=percent,
        break_minutes=break_minutes,
    )


def _entry_totals(entries: list[dict[str, Any]]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for entry in entries:
        day = entry.get("date")
        if entry.get("status") == "rejected" or not isinstance(day, str):
            continue
        totals[day] = totals.get(day, 0) + _int(entry.get("workedMinutes"))
    return totals


def _days(
    entries: list[dict[str, Any]], days: list[date], today: date, today_worked: int | None
) -> dict[str, int]:
    totals = _entry_totals(entries)
    result = {day.isoformat(): totals.get(day.isoformat(), 0) for day in days}
    if today_worked is not None and today.isoformat() in result:
        result[today.isoformat()] = today_worked
    return result


def week_days(monday: date) -> list[date]:
    """The seven days from monday through Sunday."""
    return [monday + timedelta(days=i) for i in range(7)]


def week_minutes(
    entries: list[dict[str, Any]], today: date, today_worked: int | None
) -> dict[str, int]:
    """Minutes per day, Monday to Sunday of today's week."""
    monday = today - timedelta(days=today.weekday())
    return _days(entries, week_days(monday), today, today_worked)


def month_minutes(
    entries: list[dict[str, Any]], today: date, today_worked: int | None
) -> dict[str, int]:
    """Minutes per day for every day of today's month."""
    last = calendar.monthrange(today.year, today.month)[1]
    return _days(entries, [today.replace(day=i) for i in range(1, last + 1)], today, today_worked)


def months_needed(today: date) -> list[tuple[int, int]]:
    """Months to fetch: the current one, plus those where this and last week start."""
    monday = today - timedelta(days=today.weekday())
    months = [(today.year, today.month)]
    for start in (monday, monday - timedelta(days=7)):
        if (start.year, start.month) not in months:
            months.append((start.year, start.month))
    return months


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


def _approved_on(requests: list[dict[str, Any]], day: date) -> list[dict[str, Any]]:
    """The approved requests covering day."""
    key = day.isoformat()
    return [
        request
        for request in requests
        if request.get("status") == "approved"
        and isinstance(request.get("startDate"), str)
        and isinstance(request.get("endDate"), str)
        and request["startDate"] <= key <= request["endDate"]
    ]


def day_absences(requests: list[dict[str, Any]], day: date) -> DayAbsences:
    """Combine the approved requests covering day."""
    fraction = 0.0
    reduction = False
    gain_hours = 0.0
    for request in _approved_on(requests, day):
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
    """Recorded targets of working days without absence, per weekday, oldest first.

    Days with target 0 (public holidays, free days) are no norm: a half day or
    AUA after a holiday must still be measured against a working day.
    """
    norms: dict[int, list[tuple[str, int]]] = {}
    for key in sorted(targets):
        day = date.fromisoformat(key)
        if targets[key] > 0 and not day_absences(requests, day).any:
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
    """Recorded target of the latest earlier same weekday that was a working day without absence."""
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


def _type_name(request: dict[str, Any], types: dict[str, str]) -> str:
    """Name of the request's absence type; the code itself when the type is unknown."""
    return types.get(request.get("type"), request.get("type") or "")


def upcoming_absences(
    requests: list[dict[str, Any]],
    types: dict[str, str],
    today: date,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Approved or pending absences that have not ended yet, soonest first."""
    key = today.isoformat()
    selected = [
        request
        for request in requests
        if request.get("status") in UPCOMING_STATUSES
        and isinstance(request.get("startDate"), str)
        and isinstance(request.get("endDate"), str)
        and request["endDate"] >= key
    ]
    selected.sort(key=lambda request: (request["startDate"], request["endDate"]))
    return [
        {
            "id": request.get("id"),
            "von": request["startDate"],
            "bis": request["endDate"],
            "art": _type_name(request, types),
            "tage": request.get("days"),
            "status": request["status"],
            "halbtags": request.get("halfDay"),
        }
        for request in selected[:limit]
    ]


def holiday_dates(years: Iterable[int]) -> frozenset[date]:
    """Public holidays of HOLIDAY_REGION in the given years.

    Loads the holidays package on first use, so call it from the executor.
    """
    import holidays

    return frozenset(holidays.country_holidays(*HOLIDAY_REGION, years=years))


def day_targets(
    days: list[date],
    today: date,
    targets: dict[str, int],
    requests: list[dict[str, Any]],
    feiertage: frozenset[date],
) -> dict[str, int]:
    """Target minutes per day: the recorded ones up to today, projected ones after.

    Up to today it is the effective target as the balance counts it. Later
    days are 0 on public holidays and weekends; otherwise they follow the
    weekday norm (today's target while that weekday has no norm yet), reduced
    by approved absences.
    """
    norms = _norms(targets, requests)
    result: dict[str, int] = {}
    for day in days:
        norm = _norm_before(norms, day)
        if day <= today:
            raw = targets.get(day.isoformat())
            if raw is None:
                raw = norm or 0
        elif day in feiertage or day.weekday() >= 5:
            result[day.isoformat()] = 0
            continue
        else:
            raw = norm if norm is not None else targets.get(today.isoformat(), 0)
        result[day.isoformat()] = effective_target(raw, norm, day_absences(requests, day))
    return result


def day_absence_kinds(
    days: list[date],
    requests: list[dict[str, Any]],
    types: dict[str, str],
    feiertage: frozenset[date],
) -> dict[str, dict[str, Any]]:
    """Why a day is not an ordinary working day: public holiday or approved absence.

    Overtime worked (UEB) is no absence. A full-day request wins over a half-day one.
    """
    kinds: dict[str, dict[str, Any]] = {}
    for day in days:
        if day in feiertage:
            kinds[day.isoformat()] = {"art": "Feiertag", "halbtags": False}
            continue
        covering = [
            request for request in _approved_on(requests, day) if request.get("type") != OVERTIME_GAIN
        ]
        if covering:
            request = min(covering, key=lambda item: bool(item.get("halfDay")))
            kinds[day.isoformat()] = {
                "art": _type_name(request, types),
                "halbtags": bool(request.get("halfDay")),
            }
    return kinds


def next_free_day(
    today: date,
    requests: list[dict[str, Any]],
    types: dict[str, str],
    feiertage: frozenset[date],
) -> tuple[str, str] | None:
    """First day from tomorrow on within 60 days that is free, and why.

    Free is a public holiday, a full-day absence (no overtime, no half day) or a
    weekend, in that order of precedence.
    """
    days = [today + timedelta(days=i) for i in range(1, 61)]
    kinds = day_absence_kinds(days, requests, types, feiertage)
    for day in days:
        kind = kinds.get(day.isoformat())
        if kind is not None and not kind["halbtags"]:
            return day.isoformat(), kind["art"]
        if day.weekday() >= 5:
            return day.isoformat(), "Wochenende"
    return None


class InputError(Exception):
    """Invalid action input; `key` is the translation key of the message."""

    def __init__(self, key: str, **placeholders: str) -> None:
        super().__init__(key)
        self.key = key
        self.placeholders = placeholders


def match_item(value: str, items: list[dict[str, Any]], unknown_key: str) -> str:
    """ID of the item whose ID, else name, equals value (case-insensitive)."""
    wanted = value.strip().casefold()
    for field in ("id", "name"):
        for item in items:
            if str(item.get(field) or "").strip().casefold() == wanted:
                return str(item.get("id"))
    names = ", ".join(str(item.get("name")) for item in items)
    raise InputError(unknown_key, name=value, options=names or "-")


def assignment_id(
    value: str | None, items: list[dict[str, Any]], default_id: str | None, unknown_key: str
) -> int:
    """projectId/categoryId to send: the named one, else the default, else 0 (none)."""
    if value:
        return int(match_item(value, items, unknown_key))
    return int(default_id) if default_id else 0


def stop_result(response: dict[str, Any] | None) -> dict[str, Any]:
    """Service response of a stop; None means a waiting clock was discarded."""
    if response is None:
        return {"outcome": "discarded"}
    return {
        "outcome": "saved",
        "id": response.get("id"),
        "gearbeitet": response.get("workedMinutes"),
        "pause": response.get("breakMinutes"),
    }


_BREAK_TEXT = {
    "de": {
        "title": "Timebutler: Pausenregelung",
        "intro": "{detail}\n\nNichts wurde gespeichert, die Uhr läuft weiter.",
        "apply": "Korrektur übernehmen: **Stopp mit Pausenkorrektur** drücken oder `timebutler.stop` mit `break_regulation: apply` aufrufen.",
        "ignore": "Unverändert speichern: `timebutler.stop` mit `break_regulation: ignore` aufrufen.",
        "no_remedy": "Eine automatische Korrektur ist nicht möglich.",
        "discard": "Sitzung verwerfen: **Verwerfen** drücken oder `timebutler.cancel` aufrufen.",
    },
    "en": {
        "title": "Timebutler: break regulation",
        "intro": "{detail}\n\nNothing was saved, the clock keeps running.",
        "apply": "Accept the correction: press **Stop with break correction** or call `timebutler.stop` with `break_regulation: apply`.",
        "ignore": "Save unchanged: call `timebutler.stop` with `break_regulation: ignore`.",
        "no_remedy": "The violation cannot be corrected automatically.",
        "discard": "Discard the session: press **Discard** or call `timebutler.cancel`.",
    },
}


def break_regulation_message(info: dict[str, Any], language: str) -> tuple[str, str]:
    """Title and text of the notification asking for a break-regulation decision."""
    text = _BREAK_TEXT["de" if language.lower().startswith("de") else "en"]
    parts = [text["intro"].format(detail=info.get("detailText") or "")]
    parts.append(text["no_remedy"] if info.get("hasNoRemedy") else text["apply"])
    if not info.get("obligatory"):
        parts.append(text["ignore"])
    if info.get("hasNoRemedy"):
        parts.append(text["discard"])
    return text["title"], "\n\n".join(parts)


def absence_type_code(value: str, types: dict[str, str]) -> str:
    """Absence type code from a code or a type name."""
    items = [{"id": code, "name": name} for code, name in types.items()]
    return match_item(value, items, "unknown_absence_type")


def absence_body(
    type_code: str,
    start: date,
    end: date | None,
    half_day: str | None,
    hours: float | None,
    comments: str | None,
) -> dict[str, Any]:
    """Checked body of POST /absence-requests."""
    end = end or start
    if end < start:
        raise InputError("end_before_start")
    if half_day and end != start:
        raise InputError("half_day_single_day")
    if type_code in OVERTIME_TYPES:
        if hours is None or hours <= 0:
            raise InputError("hours_required")
        if end != start:
            raise InputError("overtime_single_day")
    elif hours is not None:
        raise InputError("hours_not_allowed")
    body: dict[str, Any] = {
        "absenceTypeId": type_code,
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
    }
    if half_day:
        body["halfDay"] = half_day
    if hours is not None:
        body["hours"] = f"{hours:g}"
    if comments:
        body["comments"] = comments
    return body


def find_absence(requests: list[dict[str, Any]], day: date) -> str:
    """ID of the pending or approved request covering day."""
    key = day.isoformat()
    matches = [
        request
        for request in requests
        if request.get("status") in UPCOMING_STATUSES
        and isinstance(request.get("startDate"), str)
        and isinstance(request.get("endDate"), str)
        and request["startDate"] <= key <= request["endDate"]
    ]
    if not matches:
        raise InputError("absence_not_found", date=key)
    if len(matches) > 1:
        ids = ", ".join(
            f"{request.get('id')} ({request['halfDay']})" if request.get("halfDay") else str(request.get("id"))
            for request in matches
        )
        raise InputError("absence_ambiguous", date=key, ids=ids)
    return str(matches[0].get("id"))


def local_timestamp_ms(day: date, at: time, tz: tzinfo) -> int:
    """Unix milliseconds of a local wall-clock time."""
    return round(datetime.combine(day, at, tzinfo=tz).timestamp() * 1000)


def time_entry_body(
    day: date, start: time, end: time, break_minutes: int, remarks: str | None, tz: tzinfo
) -> dict[str, Any]:
    """Checked body of POST /time-tracking/clock-entry (without project/category)."""
    if end <= start:
        raise InputError("end_not_after_start")
    start_ms = local_timestamp_ms(day, start, tz)
    end_ms = local_timestamp_ms(day, end, tz)
    # Real duration, so a DST change on that day counts correctly.
    duration = (end_ms - start_ms) // 60_000
    if break_minutes < 0 or break_minutes >= duration:
        raise InputError("break_too_long", minutes=str(duration))
    body: dict[str, Any] = {
        "startTimestamp": start_ms,
        "endTimestamp": end_ms,
        "pauseSeconds": break_minutes * 60,
    }
    if remarks:
        body["remarks"] = remarks
    return body


def find_time_entry(entries: list[dict[str, Any]], day: date, start: time | None) -> str:
    """ID of the time entry on day (starting at start, when given)."""
    key = day.isoformat()
    matches = [entry for entry in entries if entry.get("date") == key]
    if start is not None:
        wanted = start.strftime("%H:%M")
        matches = [entry for entry in matches if entry.get("startTime") == wanted]
    if not matches:
        raise InputError("time_entry_not_found", date=key)
    if len(matches) > 1:
        times = ", ".join(sorted(str(entry.get("startTime") or "-") for entry in matches))
        raise InputError("time_entry_ambiguous", date=key, times=times)
    return str(matches[0].get("id"))
