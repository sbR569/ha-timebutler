"""Async client for the Timebutler API v2."""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import quote

import aiohttp

from .const import API_BASE, REQUEST_TIMEOUT, USER_AGENT

# Answers to write calls that mean "refused", each with its own user message.
REFUSED_STATUSES = (400, 403, 404, 409, 422)

CLOCK_PATHS = {
    "start": "/time-clock/start",
    "pause": "/time-clock/pause",
    "resume": "/time-clock/resume",
    "cancel": "/time-clock/cancel",
    "business_trip_start": "/time-clock/business-trip-start",
    "business_trip_end": "/time-clock/business-trip-end",
}


class TimebutlerError(Exception):
    """Base error of the Timebutler client."""


class TimebutlerAuthError(TimebutlerError):
    """The token was rejected (HTTP 401, or 403 on a read call)."""


class TimebutlerConnectionError(TimebutlerError):
    """Network problem, timeout, rate limit or an unexpected answer."""


class TimebutlerTimeTrackingDisabled(TimebutlerError):
    """Time tracking is not active for the company (HTTP 400)."""


class TimebutlerRequestError(TimebutlerError):
    """A write call was refused; `status` tells why (400, 403, 404, 409, 422)."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class TimebutlerClient:
    """Access to the data of the token's own user."""

    def __init__(
        self, session: aiohttp.ClientSession, token: str, base_url: str = API_BASE
    ) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        time_tracking: bool = False,
        write: bool = False,
    ) -> Any:
        """Send one request and return the decoded JSON body (None on 204)."""
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                response = await self._session.request(
                    method,
                    f"{self._base_url}{path}",
                    headers=self._headers,
                    params=params,
                    json=json,
                )
                try:
                    status = response.status
                    if status == 401 or (status == 403 and not write):
                        raise TimebutlerAuthError(f"{path}: HTTP {status}")
                    if write and status in REFUSED_STATUSES:
                        text = await response.text()
                        raise TimebutlerRequestError(
                            status, f"{method} {path}: HTTP {status} {text[:300]}"
                        )
                    if status == 400 and time_tracking:
                        raise TimebutlerTimeTrackingDisabled(path)
                    if status >= 400:
                        raise TimebutlerConnectionError(f"{path}: HTTP {status}")
                    if status == 204:
                        return None
                    return await response.json(content_type=None)
                finally:
                    response.release()
        except TimebutlerError:
            raise
        except (TimeoutError, aiohttp.ClientError, ValueError) as err:
            raise TimebutlerConnectionError(f"{path}: {err!r}") from err

    async def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        time_tracking: bool = False,
    ) -> Any:
        """GET a path and return the decoded JSON body."""
        return await self._request("GET", path, params=params, time_tracking=time_tracking)

    async def _write(self, method: str, path: str, json: dict[str, Any] | None = None) -> Any:
        """POST/DELETE a path; refusals raise TimebutlerRequestError."""
        return await self._request(method, path, json=json, write=True)

    async def get_profile(self) -> dict[str, Any]:
        """Profile of the token's user (id, name, ...)."""
        return await self._get("/user/profile")

    async def get_clock_status(self) -> dict[str, Any]:
        """State of the time clock."""
        return await self._get("/time-clock/status", time_tracking=True)

    async def get_work_summary(self) -> dict[str, Any]:
        """Today's target, worked, remaining and break minutes."""
        return await self._get("/time-tracking/work-summary", time_tracking=True)

    async def get_time_off_summary(self) -> dict[str, Any]:
        """Vacation and illness days of the current year."""
        return await self._get("/time-tracking/time-off-summary", time_tracking=True)

    async def get_absence_requests(self) -> list[dict[str, Any]]:
        """All own absence requests, in every state."""
        return await self._get("/absence-requests/my")

    async def get_absence_types(self) -> list[dict[str, Any]]:
        """Absence types; `id` is the 3-letter code used in requests."""
        return await self._get("/absence-types")

    async def get_time_entries(self, year: int, month: int) -> list[dict[str, Any]]:
        """Own time entries of one month (empty when time tracking is off)."""
        return await self._get("/time-entries/my", {"year": year, "month": month})

    async def get_projects(self) -> dict[str, Any]:
        """Active projects plus the user's default project."""
        return await self._get("/projects")

    async def get_categories(self) -> dict[str, Any]:
        """Active categories plus the user's default category."""
        return await self._get("/categories")

    async def clock(self, action: str) -> dict[str, Any]:
        """Run a time clock action (see CLOCK_PATHS) and return the new status."""
        return await self._write("POST", CLOCK_PATHS[action])

    async def clock_stop(self, body: dict[str, Any]) -> dict[str, Any] | None:
        """Stop and save the clock; None when a waiting clock was discarded."""
        return await self._write("POST", "/time-clock/stop", body)

    async def get_substitutes(self) -> list[dict[str, Any]]:
        """Employees that may substitute the user."""
        return await self._get("/substitute-employees")

    async def create_absence(self, body: dict[str, Any]) -> dict[str, Any]:
        """Submit an absence request; returns it in its new state."""
        return await self._write("POST", "/absence-requests", body)

    async def delete_absence(self, absence_id: str, reason: str | None) -> dict[str, Any]:
        """Delete an absence request, or ask for its cancellation."""
        body = {"reason": reason} if reason else None
        return await self._write("DELETE", f"/absence-requests/{quote(absence_id, safe='')}", body)

    async def add_clock_entry(self, body: dict[str, Any]) -> dict[str, Any]:
        """Create a completed time entry from start/end timestamps."""
        return await self._write("POST", "/time-tracking/clock-entry", body)

    async def delete_time_entry(self, entry_id: str) -> dict[str, Any]:
        """Delete a time entry, or ask for its deletion."""
        return await self._write("DELETE", f"/time-entries/{quote(entry_id, safe='')}")
