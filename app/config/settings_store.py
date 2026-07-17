"""Persistent settings store — %APPDATA%\\Vigil\\config.json.

Simple JSON-based config with defaults. Loads at startup, saves on change.
No cloud, no telemetry.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _config_dir() -> Path:
    """Return the Vigil config directory path."""
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        return Path(appdata) / "Vigil"
    # Fallback for non-Windows
    return Path.home() / ".config" / "vigil"


def _config_path() -> Path:
    return _config_dir() / "config.json"


# ── Default settings ─────────────────────────────────────────────

_DEFAULTS = {
    # AI settings
    "ai_endpoint": "http://127.0.0.1:11434",
    "ai_model": "",
    "ai_tone": "Neutral",
    "ai_length": "Standard",
    "ai_findings_enabled": True,
    "ai_startups_enabled": True,
    "ai_cleanup_hints_enabled": False,
    "ai_explain_risky_only": False,
    "ai_timeout": 180,
    "ai_max_concurrent": 3,
    "ai_explanation_language": "English",

    # Appearance
    "theme": "forest",

    # Language
    "ui_language": "English",

    # Window close behavior when background work (scan / cleanup / AI) is
    # running. One of:
    #   "ask"        — prompt with the close dialog (default)
    #   "background" — always minimize to the tray and keep working
    #   "quit"       — always stop the work and exit
    "close_behavior": "ask",

    # Scan behavior
    "confirm_risky_cleanup": True,
    # Follow into other drives/volumes (mounted disks, junctions to another
    # volume). Off by default so a scan stays on the chosen drive.
    "scan_cross_volumes": False,

    # Cleanup safety
    "perm_delete_enabled": False,
}


class SettingsStore:
    """Singleton-style settings manager. Load/save from config.json."""

    def __init__(self):
        self._data: dict = dict(_DEFAULTS)
        self._path = _config_path()
        self.load()

    def load(self):
        """Load settings from disk. Missing keys get defaults."""
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                if isinstance(saved, dict):
                    for key in _DEFAULTS:
                        if key in saved:
                            self._data[key] = saved[key]
            except (json.JSONDecodeError, OSError):
                pass
        # Permanent delete is not wired in the UI yet; keep cleanup Recycle Bin-only.
        self._data["perm_delete_enabled"] = False

    def save(self):
        """Persist current settings to disk."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
        except OSError:
            pass

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default if default is not None else _DEFAULTS.get(key))

    def set(self, key: str, value: Any):
        self._data[key] = value

    def set_and_save(self, key: str, value: Any):
        """Set a value and immediately persist."""
        self._data[key] = value
        self.save()

    def all(self) -> dict:
        return dict(self._data)

    @property
    def config_path(self) -> str:
        return str(self._path)

    def reset(self):
        """Reset to defaults."""
        self._data = dict(_DEFAULTS)
        self.save()
