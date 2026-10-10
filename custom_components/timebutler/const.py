"""Constants for the Timebutler integration."""

from datetime import timedelta

DOMAIN = "timebutler"
VERSION = "0.1.0"

API_BASE = "https://app.timebutler.com/api/v2"
# Timebutler drops TLS connections that carry curl's default User-Agent.
USER_AGENT = f"ha-timebutler/{VERSION}"
REQUEST_TIMEOUT = 20

LIVE_INTERVAL = timedelta(seconds=60)
BACKGROUND_INTERVAL = timedelta(minutes=15)
UPCOMING_LIMIT = 5
