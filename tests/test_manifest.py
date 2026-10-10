"""Manifest, HACS metadata and the User-Agent stay in sync."""
import json
from pathlib import Path

from custom_components.timebutler.const import USER_AGENT, VERSION

ROOT = Path(__file__).parent.parent


def test_manifest_version_matches_user_agent():
    manifest = json.loads(
        (ROOT / "custom_components/timebutler/manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["version"] == VERSION
    assert manifest["domain"] == "timebutler"
    assert manifest["requirements"] == []
    assert USER_AGENT == f"ha-timebutler/{VERSION}"


def test_hacs_metadata():
    hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
    assert hacs["name"] == "Timebutler"
    assert hacs["homeassistant"] == "2025.3.0"
