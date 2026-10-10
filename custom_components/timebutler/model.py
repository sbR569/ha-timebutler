"""Pure data shaping for the Timebutler integration (no Home Assistant imports)."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
import math
from typing import Any

CLOCK_STATES = ("idle", "running", "paused", "waiting")
CLOCKED_IN_STATES = ("running", "paused")
UPCOMING_STATUSES = ("approved", "pending")


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
    equals today's finished entries or those plus the running session.
    """
    if clock is None or not clock.clocked_in:
        return False
    session = _session_today(clock, now)
    if session <= 2:
        return False
    finished = _entry_totals(entries).get(now.date().isoformat(), 0)
    return _int(summary.get("workedMinutes")) >= finished + session - 2


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


def week_minutes(
    entries: list[dict[str, Any]], today: date, today_worked: int | None
) -> dict[str, int]:
    """Minutes per day, Monday to Sunday of today's week."""
    monday = today - timedelta(days=today.weekday())
    return _days(entries, [monday + timedelta(days=i) for i in range(7)], today, today_worked)


def month_minutes(
    entries: list[dict[str, Any]], today: date, today_worked: int | None
) -> dict[str, int]:
    """Minutes per day for every day of today's month."""
    last = calendar.monthrange(today.year, today.month)[1]
    return _days(entries, [today.replace(day=i) for i in range(1, last + 1)], today, today_worked)


def months_needed(today: date) -> list[tuple[int, int]]:
    """Months to fetch: the current one, plus the previous if the week starts there."""
    monday = today - timedelta(days=today.weekday())
    months = [(today.year, today.month)]
    if (monday.year, monday.month) != months[0]:
        months.append((monday.year, monday.month))
    return months


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
            "von": request["startDate"],
            "bis": request["endDate"],
            "art": types.get(request.get("type"), request.get("type") or ""),
            "tage": request.get("days"),
            "status": request["status"],
            "halbtags": request.get("halfDay"),
        }
        for request in selected[:limit]
    ]
