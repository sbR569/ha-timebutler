"""Async client for the Timebutler API v2."""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

from .const import API_BASE, REQUEST_TIMEOUT, USER_AGENT


class TimebutlerError(Exception):
    """Base error of the Timebutler client."""


class TimebutlerAuthError(TimebutlerError):
    """The token was rejected (HTTP 401/403)."""


class TimebutlerConnectionError(TimebutlerError):
    """Network problem, timeout, rate limit or an unexpected answer."""


class TimebutlerTimeTrackingDisabled(TimebutlerError):
    """Time tracking is not active for the company (HTTP 400)."""


class TimebutlerClient:
    """Read-only access to the data of the token's own user."""

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

    async def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        time_tracking: bool = False,
    ) -> Any:
        """GET a path and return the decoded JSON body."""
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                response = await self._session.get(
                    f"{self._base_url}{path}", headers=self._headers, params=params
                )
                try:
                    if response.status in (401, 403):
                        raise TimebutlerAuthError(f"{path}: HTTP {response.status}")
                    if response.status == 400 and time_tracking:
                        raise TimebutlerTimeTrackingDisabled(path)
                    if response.status >= 400:
                        raise TimebutlerConnectionError(f"{path}: HTTP {response.status}")
                    return await response.json(content_type=None)
                finally:
                    response.release()
        except TimebutlerError:
            raise
        except (TimeoutError, aiohttp.ClientError, ValueError) as err:
            raise TimebutlerConnectionError(f"{path}: {err!r}") from err

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
