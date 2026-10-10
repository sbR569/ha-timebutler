"""Query the Timebutler API v2 with TIMEBUTLER_TOKEN and print what the integration uses.

Run while clocked in for at least 5 minutes: the last lines say whether
/time-tracking/work-summary already counts the running clock session
(const.WORK_SUMMARY_INCLUDES_RUNNING). The token is never printed.
Output contains your own Timebutler data - do not commit or paste it publicly.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date

BASE = "https://app.timebutler.com/api/v2"
USER_AGENT = "ha-timebutler/live-check"


def get(path: str, token: str) -> tuple[int, object]:
    request = urllib.request.Request(
        BASE + path,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as err:
        return err.code, err.read().decode("utf-8", "replace")[:300]


def main() -> None:
    token = os.environ.get("TIMEBUTLER_TOKEN")
    if not token:
        sys.exit("TIMEBUTLER_TOKEN is not set")
    today = date.today()
    entries_path = f"/time-entries/my?year={today.year}&month={today.month}"
    paths = [
        "/user/profile",
        "/time-clock/status",
        "/time-tracking/work-summary",
        "/time-tracking/time-off-summary",
        "/absence-requests/my",
        "/absence-types",
        entries_path,
    ]
    results = {}
    for path in paths:
        status, body = get(path, token)
        results[path] = body
        print(f"== {path} -> HTTP {status}")
        print(json.dumps(body, indent=1, ensure_ascii=False)[:1500])

    clock = results["/time-clock/status"]
    summary = results["/time-tracking/work-summary"]
    entries = results[entries_path]
    if not (isinstance(clock, dict) and isinstance(summary, dict) and isinstance(entries, list)):
        sys.exit("\nClock, summary or entries did not return JSON - see above.")
    done = sum(
        e.get("workedMinutes") or 0
        for e in entries
        if e.get("date") == today.isoformat() and e.get("status") != "rejected"
    )
    session = (clock.get("workTimeElapsedSeconds") or 0) // 60
    worked = summary.get("workedMinutes") or 0
    print(
        f"\nclock={clock.get('status')} session={session} min, "
        f"summary.workedMinutes={worked}, today's finished entries={done} min"
    )
    if clock.get("status") in ("running", "paused") and session >= 5:
        includes = abs(worked - (done + session)) <= 2
        print(f"=> WORK_SUMMARY_INCLUDES_RUNNING = {includes}")
    else:
        print("=> Clock in for at least 5 minutes and run again to decide.")


if __name__ == "__main__":
    main()
