"""Vigil i18n — translation module.

Usage:
    from app.i18n import tr
    label.setText(tr("Start scan"))
    label.setText(tr("{count:,} items", count=n))

Call init_language(store) once at startup before any screen is built.
Translations live in app/locales/<code>.json, keyed by the English string.

English needs no file: an untranslated key falls back to the key itself. That
also makes a *partial* locale file useful — translated strings appear in the
chosen language, the rest stay English — instead of an incomplete file being
all-or-nothing.
"""
from __future__ import annotations

import json
from pathlib import Path

# Display name → locale file code. English is the implicit source language and
# deliberately has no file.
LANGUAGES: dict[str, str] = {
    "English": "en",
    "Ukrainian": "uk",
    "Spanish": "es",
    "German": "de",
    "French": "fr",
}

_lang: str = "English"
_cache: dict[str, dict[str, str]] = {}


def _locales_dir() -> Path:
    return Path(__file__).parent / "locales"


def _load(lang_code: str) -> dict[str, str]:
    """Load and cache translations for *lang_code* from its JSON file."""
    if lang_code not in _cache:
        path = _locales_dir() / f"{lang_code}.json"
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            _cache[lang_code] = {k: v for k, v in data.items()
                                 if not k.startswith("__") and isinstance(v, str) and v}
        except Exception:
            _cache[lang_code] = {}
    return _cache[lang_code]


def available_languages() -> list[str]:
    """Languages offered in the UI: English plus every locale file present.

    Driven by what actually ships, so a language cannot be advertised in the
    picker before its file exists.
    """
    names = ["English"]
    for name, code in LANGUAGES.items():
        if code == "en":
            continue
        if (_locales_dir() / f"{code}.json").exists():
            names.append(name)
    return names


def explanation_languages() -> list[str]:
    """Languages the AI can be asked to answer in.

    Deliberately not available_languages(): what the model writes has nothing to
    do with whether Vigil ships a locale file. Gating this on a UI translation
    meant a user running a multilingual model could not ask for German simply
    because de.json did not exist yet.
    """
    return list(LANGUAGES)


def coverage(lang: str, keys: list[str] | None = None) -> float:
    """Fraction of *keys* translated for *lang* (1.0 for English)."""
    code = LANGUAGES.get(lang, "en")
    if code == "en":
        return 1.0
    table = _load(code)
    if not keys:
        return 1.0 if table else 0.0
    hit = sum(1 for k in keys if table.get(k))
    return hit / len(keys) if keys else 0.0


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

    Falls back to *key* itself when the language is English or the key is not
    translated, so a partial locale file degrades to English per string.
    """
    code = LANGUAGES.get(_lang, "en")
    text = _load(code).get(key, key) if code != "en" else key
    if kwargs:
        try:
            return text.format(**kwargs)
        except Exception:
            return text
    return text
