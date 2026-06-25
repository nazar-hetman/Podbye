"""Vigil i18n — minimal two-language translation module.

Usage:
    from app.i18n import tr
    label.setText(tr("Start scan"))
    label.setText(tr("{count:,} items", count=n))

Call init_language(store) once at startup before any screen is built.
Translations are loaded from app/locales/<lang_code>.json on demand.
"""
from __future__ import annotations

import json
from pathlib import Path

_lang: str = "English"
_cache: dict[str, dict[str, str]] = {}


def _load(lang_code: str) -> dict[str, str]:
    """Load and cache translations for *lang_code* from its JSON file."""
    if lang_code not in _cache:
        path = Path(__file__).parent / "locales" / f"{lang_code}.json"
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            _cache[lang_code] = {k: v for k, v in data.items() if not k.startswith("__")}
        except Exception:
            _cache[lang_code] = {}
    return _cache[lang_code]


def init_language(store) -> None:
    """Read ui_language from settings store and activate it."""
    global _lang
    if store:
        _lang = store.get("ui_language", "English")


def set_language(lang: str) -> None:
    global _lang
    _lang = lang


def get_language() -> str:
    return _lang


def tr(key: str, **kwargs) -> str:
    """Return the translated string for *key* in the active language.

    Falls back to *key* itself if no translation is found (so English is
    the implicit default — no separate English dict needed).
    """
    if _lang == "Ukrainian":
        text = _load("uk").get(key, key)
    else:
        text = key
    if kwargs:
        try:
            return text.format(**kwargs)
        except Exception:
            return text
    return text
