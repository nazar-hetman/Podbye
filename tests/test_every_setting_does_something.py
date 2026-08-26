"""A control in Settings must change something.

"Cleanup hints" was a checkbox in the AI panel from the first commit. Ticking
it wrote ``ai_cleanup_hints_enabled`` to config.json, and no line of Podbye ever
read that key back — the toggle had never been wired to anything. It sat beside
two toggles that do work, which is what made it costly: it taught the user that
the panel means what it says.

This is the same rule test_settings_honesty.py already applies one case at a
time — About must not list folders nothing creates, the Cleanup panel must not
show a radio that can never be chosen — enforced across the whole store instead
of one finding at a time.
"""
import re
from pathlib import Path

import pytest

from app.config.settings_store import _DEFAULTS

APP = Path(__file__).resolve().parents[1] / "app"

# Where a key is *written*: neither counts as somebody acting on the value.
_DECLARING_FILES = {"settings_store.py", "settings.py"}

# Keys whose only reader is the Settings screen itself, by design. Both exist
# so the screen can remember how the active endpoint was chosen; the endpoint
# it produces (ai_endpoint) is what the rest of the app reads.
_UI_ONLY_KEYS = {"ai_endpoint_mode", "ai_server_endpoint"}


def _readers(key: str) -> list[str]:
    """Files outside the declaring pair that mention *key*."""
    pattern = re.compile(rf"""["']{re.escape(key)}["']""")
    hits = []
    for path in APP.rglob("*.py"):
        if path.name in _DECLARING_FILES or "__pycache__" in path.parts:
            continue
        if pattern.search(path.read_text(encoding="utf-8")):
            hits.append(str(path.relative_to(APP)))
    return hits


@pytest.mark.parametrize("key", sorted(set(_DEFAULTS) - _UI_ONLY_KEYS))
def test_a_stored_setting_is_read_by_something(key):
    assert _readers(key), (
        f"{key!r} is written by Settings and read by nothing — either wire the "
        f"control up or take it off the screen")


def test_the_dead_cleanup_hints_toggle_is_gone():
    from app.screens import settings as settings_module
    source = Path(settings_module.__file__).read_text(encoding="utf-8")
    assert "ai_cleanup_hints_enabled" not in source
    assert "ai_cleanup_hints_enabled" not in _DEFAULTS
