"""Persistent settings store — %APPDATA%\\Podbye\\config.json.

Simple JSON-based config with defaults. Loads at startup, saves on change.
No cloud, no telemetry.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _config_dir() -> Path:
    """Return the Podbye config directory path."""
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        return Path(appdata) / "Podbye"
    # Fallback for non-Windows
    return Path.home() / ".config" / "podbye"


def _config_path() -> Path:
    return _config_dir() / "config.json"


# ── Default settings ─────────────────────────────────────────────

_DEFAULTS = {
    # AI settings
    # ai_endpoint is the ACTIVE endpoint every AI call uses. ai_endpoint_mode
    # picks where it comes from: "local" (the built-in loopback address) or
    # "server" (a custom LAN address). ai_server_endpoint remembers the custom
    # address separately, so toggling back to Local never discards what the
    # user typed.
    "ai_endpoint": "http://127.0.0.1:11434",
    "ai_endpoint_mode": "local",
    "ai_server_endpoint": "",
    "ai_model": "",
    "ai_tone": "Neutral",
    "ai_length": "Standard",
    # Bulk AI is OFF by default: a scan can produce hundreds of entities and
    # explaining them all at once chokes a local model. Per-item "Ask AI" still
    # works (explain_item bypasses this toggle), and this can be switched on for
    # long background/overnight runs.
    "ai_findings_enabled": False,
    "ai_startups_enabled": True,
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

    # Paths the user marked Keep — never selected, never deleted. Listed here
    # because load() only restores keys it knows about, so a setting missing
    # from this table is written to config.json and then read back as absent.
    # See app/services/keep_list.py.
    "kept_paths": [],
}


def _fresh_defaults() -> dict:
    """A copy of the defaults that shares no mutable value with the table.

    ``dict(_DEFAULTS)`` is shallow, so every store would hand out the *same*
    list object for kept_paths and one instance mutating it would change them
    all.
    """
    return {k: (list(v) if isinstance(v, list) else v)
            for k, v in _DEFAULTS.items()}


class SettingsStore:
    """Singleton-style settings manager. Load/save from config.json."""

    def __init__(self):
        self._data: dict = _fresh_defaults()
        self._path = _config_path()
        self._last_save_error = ""
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

    def save(self) -> bool:
        """Persist current settings to disk and report whether it succeeded.

        Settings apply immediately in memory, but that is not a substitute for
        durable storage.  Callers that have a UI can now tell the user when a
        permissions, disk, or file-system error means the choice will be lost
        on the next launch.
        """
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
        except OSError as exc:
            self._last_save_error = str(exc)
            return False
        self._last_save_error = ""
        return True

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default if default is not None else _DEFAULTS.get(key))

    def set(self, key: str, value: Any):
        self._data[key] = value

    def set_and_save(self, key: str, value: Any) -> bool:
        """Set a value and immediately persist."""
        self._data[key] = value
        return self.save()

    def all(self) -> dict:
        return dict(self._data)

    @property
    def config_path(self) -> str:
        return str(self._path)

    @property
    def last_save_error(self) -> str:
        """The most recent persistence error, or an empty string."""
        return self._last_save_error

    def reset(self) -> bool:
        """Reset ordinary preferences while retaining durable exclusions.

        Kept paths are a standing cleanup safety instruction, not a cosmetic
        preference.  The Settings confirmation promises they survive a reset,
        and retaining them also prevents a reset from unexpectedly making
        previously excluded data eligible for cleanup.
        """
        kept_paths = list(self._data.get("kept_paths", []) or [])
        self._data = _fresh_defaults()
        self._data["kept_paths"] = kept_paths
        return self.save()
