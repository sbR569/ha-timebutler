"""Pure data shaping in model.py."""
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from custom_components.timebutler.model import (
    ClockState,
    InputError,
    TodaySummary,
    assignment_id,
    break_regulation_message,
    match_item,
    month_minutes,
    months_needed,
    parse_clock,
    stop_result,
    summary_includes_running,
    today_summary,
    upcoming_absences,
    week_minutes,
)
from tests.common import load_fixture_json

BERLIN = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 10, 7, 10, 0, tzinfo=BERLIN)
TODAY = NOW.date()
SUMMARY = load_fixture_json("work_summary")
ENTRIES = load_fixture_json("entries_2026_10")
TYPES = {"URL": "Urlaub", "GLZ": "Gleitzeit"}


def _clock(status="running", started=datetime(2026, 10, 7, 5, 0, tzinfo=UTC), minutes=180, break_minutes=0):
    return ClockState(
        status=status,
        started=started,
        business_trip=False,
        session_minutes=minutes,
        session_break_minutes=break_minutes,
    )


def test_parse_clock_running():
    clock = parse_clock(load_fixture_json("clock_running"))

    assert clock.status == "running"
    assert clock.started == datetime(2026, 10, 7, 14, 0, tzinfo=UTC)
    assert clock.session_minutes == 180
    assert clock.session_break_minutes == 0
    assert clock.business_trip is False
    assert clock.clocked_in is True


def test_parse_clock_idle():
    clock = parse_clock(load_fixture_json("clock_idle"))

    assert clock.status == "idle"
    assert clock.started is None
    assert clock.clocked_in is False


def test_parse_clock_tolerates_missing_and_unknown_fields():
    clock = parse_clock({"status": "teleporting", "startTimestamp": None})

    assert clock.status is None
    assert clock.started is None
    assert clock.session_minutes == 0
    assert clock.business_trip is False
    assert clock.clocked_in is False


def test_today_adds_running_session_when_summary_excludes_it():
    today = today_summary(SUMMARY, _clock(minutes=180, break_minutes=10), includes_running=False, now=NOW)

    assert today == TodaySummary(commitment=480, worked=240, remaining=240, percent=50, break_minutes=25)


def test_today_counts_paused_session():
    today = today_summary(SUMMARY, _clock(status="paused", minutes=100), includes_running=False, now=NOW)

    assert today.worked == 160


def test_today_ignores_clock_when_clocked_out():
    today = today_summary(SUMMARY, _clock(status="idle", started=None, minutes=0), includes_running=False, now=NOW)

    assert today == TodaySummary(commitment=480, worked=60, remaining=420, percent=13, break_minutes=15)


def test_today_uses_summary_as_is_when_it_includes_running():
    today = today_summary(SUMMARY, _clock(), includes_running=True, now=NOW)

    assert today == TodaySummary(commitment=480, worked=60, remaining=420, percent=13, break_minutes=15)


def test_today_session_across_midnight_counts_only_since_midnight():
    now = datetime(2026, 10, 7, 2, 30, tzinfo=BERLIN)
    # Clocked in at 22:00 Berlin the evening before; 4.5 h elapsed.
    clock = _clock(started=datetime(2026, 10, 6, 20, 0, tzinfo=UTC), minutes=270)
    summary = {"commitmentMinutes": 0, "workedMinutes": 0, "remainingMinutes": 0, "percentDone": 0, "breakMinutes": 0}

    assert today_summary(summary, clock, includes_running=False, now=now).worked == 150


def test_today_without_target_has_no_percentage_and_counts_all_as_overtime():
    summary = {"commitmentMinutes": 0, "workedMinutes": 90, "breakMinutes": 0}

    today = today_summary(summary, None, includes_running=False, now=NOW)

    assert today == TodaySummary(commitment=0, worked=90, remaining=-90, percent=0, break_minutes=0)


def test_week_sums_per_day_skips_rejected_and_uses_live_today():
    assert week_minutes(ENTRIES, TODAY, today_worked=240) == {
        "2026-10-05": 450,
        "2026-10-06": 500,
        "2026-10-07": 240,
        "2026-10-08": 0,
        "2026-10-09": 0,
        "2026-10-10": 0,
        "2026-10-11": 0,
    }


def test_week_falls_back_to_entries_for_today_without_live_value():
    assert week_minutes(ENTRIES, TODAY, today_worked=None)["2026-10-07"] == 60


def test_month_has_every_day_and_sums_entries():
    month = month_minutes(ENTRIES, TODAY, today_worked=240)

    assert len(month) == 31
    assert month["2026-10-01"] == 480
    assert month["2026-10-02"] == 420
    assert month["2026-10-07"] == 240
    assert month["2026-10-31"] == 0
    assert sum(month.values()) == 2090


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (date(2026, 10, 7), [(2026, 10)]),
        (date(2026, 10, 1), [(2026, 10), (2026, 9)]),
        (date(2027, 1, 1), [(2027, 1), (2026, 12)]),
    ],
)
def test_months_needed(today, expected):
    assert months_needed(today) == expected


def test_upcoming_filters_sorts_and_names():
    result = upcoming_absences(load_fixture_json("absences"), TYPES, TODAY)

    assert [a["von"] for a in result] == ["2026-10-26", "2026-11-20", "2026-12-21"]
    assert result[0] == {
        "von": "2026-10-26",
        "bis": "2026-10-30",
        "art": "Urlaub",
        "tage": 5.0,
        "status": "approved",
        "halbtags": None,
    }
    assert result[1]["art"] == "XYZ"
    assert result[1]["halbtags"] == "morning"
    assert result[2]["status"] == "pending"


def test_upcoming_includes_absence_running_today():
    requests = [{"type": "GLZ", "startDate": "2026-10-06", "endDate": "2026-10-08", "days": 3.0, "status": "approved", "halfDay": None}]

    result = upcoming_absences(requests, TYPES, TODAY)

    assert result[0]["von"] == "2026-10-06"
    assert result[0]["art"] == "Gleitzeit"


def test_upcoming_respects_limit():
    requests = [
        {"type": "URL", "startDate": f"2026-11-{day:02d}", "endDate": f"2026-11-{day:02d}", "days": 1.0, "status": "approved", "halfDay": None}
        for day in range(9, 0, -1)
    ]

    result = upcoming_absences(requests, TYPES, TODAY, limit=5)

    assert [a["von"] for a in result] == ["2026-11-01", "2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05"]


def test_upcoming_empty():
    assert upcoming_absences([], TYPES, TODAY) == []


def test_detects_summary_that_already_counts_the_session():
    summary = {**SUMMARY, "workedMinutes": 240}

    assert summary_includes_running(summary, _clock(minutes=180), ENTRIES, NOW) is True


def test_detects_summary_without_the_session():
    assert summary_includes_running(SUMMARY, _clock(minutes=180), ENTRIES, NOW) is False


def test_detection_is_false_when_clocked_out():
    assert summary_includes_running(SUMMARY, _clock(status="idle", started=None, minutes=0), ENTRIES, NOW) is False


def test_detection_after_midnight_uses_only_todays_part_of_the_session():
    now = datetime(2026, 10, 7, 2, 30, tzinfo=BERLIN)
    clock = _clock(started=datetime(2026, 10, 6, 20, 0, tzinfo=UTC), minutes=270)
    summary = {"commitmentMinutes": 0, "workedMinutes": 150, "breakMinutes": 0}

    assert summary_includes_running(summary, clock, [], now) is True


@pytest.mark.parametrize("worked", [237, 210])
def test_detection_tolerates_a_lagging_or_deducted_summary(worked):
    # Finished entries 60 + session 180; the summary is a few minutes behind
    # or Timebutler already deducted a legal break from the running session.
    summary = {**SUMMARY, "workedMinutes": worked}

    assert summary_includes_running(summary, _clock(minutes=180), ENTRIES, NOW) is True


PROJECTS = load_fixture_json("projects")["projects"]
BREAK_INFO = load_fixture_json("stop_confirmation")["breakRegulation"]


def test_match_item_by_id_or_name():
    assert match_item("6", PROJECTS, "unknown_project") == "6"
    assert match_item("  kundenprojekt a ", PROJECTS, "unknown_project") == "5"


def test_match_item_prefers_ids_over_names():
    items = [{"id": "1", "name": "2"}, {"id": "2", "name": "Zwei"}]

    assert match_item("2", items, "unknown_project") == "2"


def test_match_item_unknown_lists_names():
    with pytest.raises(InputError) as err:
        match_item("Extern", PROJECTS, "unknown_project")

    assert err.value.key == "unknown_project"
    assert err.value.placeholders == {"name": "Extern", "options": "Kundenprojekt A, Intern"}


def test_match_item_unknown_without_items():
    with pytest.raises(InputError) as err:
        match_item("Extern", [], "unknown_category")

    assert err.value.placeholders["options"] == "-"


def test_assignment_id_uses_value_then_default_then_zero():
    assert assignment_id("Intern", PROJECTS, "5", "unknown_project") == 6
    assert assignment_id(None, PROJECTS, "5", "unknown_project") == 5
    assert assignment_id("", [], None, "unknown_project") == 0


def test_stop_result():
    assert stop_result(load_fixture_json("stop_saved")) == {
        "outcome": "saved",
        "id": "98765",
        "gearbeitet": 180,
        "pause": 0,
    }
    assert stop_result(None) == {"outcome": "discarded"}


def test_break_regulation_message_offers_apply_and_ignore():
    title, message = break_regulation_message(BREAK_INFO, "de")

    assert title == "Timebutler: Pausenregelung"
    assert message.startswith("Required change: working time -0:30, break +0:30")
    assert "break_regulation: apply" in message
    assert "break_regulation: ignore" in message
    assert "timebutler.cancel" not in message


def test_break_regulation_message_obligatory_hides_ignore():
    _, message = break_regulation_message({**BREAK_INFO, "obligatory": True}, "en")

    assert "break_regulation: apply" in message
    assert "ignore" not in message


def test_break_regulation_message_without_remedy_offers_discard():
    info = {**BREAK_INFO, "hasNoRemedy": True, "obligatory": True}

    title, message = break_regulation_message(info, "en-GB")

    assert title == "Timebutler: break regulation"
    assert "break_regulation: apply" not in message
    assert "timebutler.cancel" in message
