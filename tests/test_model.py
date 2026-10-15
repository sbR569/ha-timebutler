"""Pure data shaping in model.py."""
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from custom_components.timebutler.model import (
    ClockState,
    DayAbsences,
    InputError,
    TodaySummary,
    absence_body,
    absence_type_code,
    assignment_id,
    break_regulation_message,
    day_absences,
    effective_target,
    find_absence,
    find_time_entry,
    format_balance,
    local_timestamp_ms,
    match_item,
    month_minutes,
    months_between,
    months_needed,
    overtime_days,
    parse_balance,
    parse_clock,
    stop_result,
    time_entry_body,
    summary_includes_running,
    today_summary,
    upcoming_absences,
    week_minutes,
    weekday_norm,
)
from tests.common import absence_dto, entry_dto, load_fixture_json

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
        "id": "3",
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


def test_absence_type_by_code_or_name():
    assert absence_type_code("url", TYPES) == "URL"
    assert absence_type_code("Gleitzeit", TYPES) == "GLZ"


def test_absence_type_unknown():
    with pytest.raises(InputError) as err:
        absence_type_code("Sabbatical", TYPES)

    assert err.value.key == "unknown_absence_type"
    assert err.value.placeholders == {"name": "Sabbatical", "options": "Urlaub, Gleitzeit"}


def test_absence_body_full_days():
    body = absence_body("URL", date(2026, 11, 9), date(2026, 11, 13), None, None, "Herbst")

    assert body == {"absenceTypeId": "URL", "startDate": "2026-11-09", "endDate": "2026-11-13", "comments": "Herbst"}


def test_absence_body_single_half_day_defaults_end():
    body = absence_body("URL", date(2026, 11, 9), None, "afternoon", None, None)

    assert body == {"absenceTypeId": "URL", "startDate": "2026-11-09", "endDate": "2026-11-09", "halfDay": "afternoon"}


def test_absence_body_overtime_hours():
    body = absence_body("UEB", date(2026, 11, 9), None, None, 1.5, None)

    assert body["hours"] == "1.5"
    assert absence_body("AUA", date(2026, 11, 9), None, None, 8.0, None)["hours"] == "8"


@pytest.mark.parametrize(
    ("args", "key"),
    [
        (("URL", date(2026, 11, 9), date(2026, 11, 8), None, None), "end_before_start"),
        (("URL", date(2026, 11, 9), date(2026, 11, 10), "morning", None), "half_day_single_day"),
        (("UEB", date(2026, 11, 9), None, None, None), "hours_required"),
        (("UEB", date(2026, 11, 9), None, None, 0.0), "hours_required"),
        (("AUA", date(2026, 11, 9), date(2026, 11, 10), None, 2.0), "overtime_single_day"),
        (("URL", date(2026, 11, 9), None, None, 2.0), "hours_not_allowed"),
    ],
)
def test_absence_body_rejects(args, key):
    with pytest.raises(InputError) as err:
        absence_body(*args, None)

    assert err.value.key == key


def test_find_absence_covering_the_day():
    requests = load_fixture_json("absences")

    assert find_absence(requests, date(2026, 10, 28)) == "3"
    assert find_absence(requests, date(2026, 12, 21)) == "2"


def test_find_absence_ignores_rejected():
    with pytest.raises(InputError) as err:
        find_absence(load_fixture_json("absences"), date(2026, 11, 3))

    assert err.value.key == "absence_not_found"
    assert err.value.placeholders == {"date": "2026-11-03"}


def test_find_absence_ambiguous_half_days():
    requests = [
        {"id": "5", "startDate": "2026-11-20", "endDate": "2026-11-20", "status": "approved", "halfDay": "morning"},
        {"id": "9", "startDate": "2026-11-20", "endDate": "2026-11-20", "status": "pending", "halfDay": "afternoon"},
    ]

    with pytest.raises(InputError) as err:
        find_absence(requests, date(2026, 11, 20))

    assert err.value.key == "absence_ambiguous"
    assert err.value.placeholders == {"date": "2026-11-20", "ids": "5 (morning), 9 (afternoon)"}


def test_local_timestamp_ms():
    assert local_timestamp_ms(date(2026, 10, 7), time(8, 0), BERLIN) == 1791352800000


def test_time_entry_body():
    body = time_entry_body(date(2026, 10, 7), time(8, 0), time(16, 30), 30, "Nachtrag", BERLIN)

    assert body == {
        "startTimestamp": 1791352800000,
        "endTimestamp": 1791383400000,
        "pauseSeconds": 1800,
        "remarks": "Nachtrag",
    }


def test_time_entry_body_on_dst_change_day():
    # 2026-10-25: clocks go back 03:00 -> 02:00, so 01:00-05:00 lasts 5 hours.
    body = time_entry_body(date(2026, 10, 25), time(1, 0), time(5, 0), 260, None, BERLIN)

    assert body["endTimestamp"] - body["startTimestamp"] == 300 * 60_000
    with pytest.raises(InputError) as err:
        time_entry_body(date(2026, 10, 25), time(1, 0), time(5, 0), 300, None, BERLIN)
    assert err.value.placeholders == {"minutes": "300"}


@pytest.mark.parametrize(
    ("start", "end", "break_minutes", "key"),
    [
        (time(16, 30), time(8, 0), 0, "end_not_after_start"),
        (time(8, 0), time(8, 0), 0, "end_not_after_start"),
        (time(8, 0), time(16, 30), 510, "break_too_long"),
    ],
)
def test_time_entry_body_rejects(start, end, break_minutes, key):
    with pytest.raises(InputError) as err:
        time_entry_body(date(2026, 10, 7), start, end, break_minutes, None, BERLIN)

    assert err.value.key == key


def test_find_time_entry():
    assert find_time_entry(ENTRIES, date(2026, 10, 7), None) == "9"
    assert find_time_entry(ENTRIES, date(2026, 10, 6), time(18, 0)) == "8"


def test_find_time_entry_ambiguous():
    with pytest.raises(InputError) as err:
        find_time_entry(ENTRIES, date(2026, 10, 6), None)

    assert err.value.key == "time_entry_ambiguous"
    assert err.value.placeholders == {"date": "2026-10-06", "times": "08:00, 18:00"}


def test_find_time_entry_missing():
    with pytest.raises(InputError) as err:
        find_time_entry(ENTRIES, date(2026, 10, 7), time(9, 0))

    assert err.value.key == "time_entry_not_found"
    assert err.value.placeholders == {"date": "2026-10-07"}


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
