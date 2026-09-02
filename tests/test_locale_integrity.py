"""A locale file must be safe to interpolate and honest about its coverage.

tr() swallows a formatting error and falls back to the untranslated key, so a
translation that drops or renames a placeholder does not crash — it silently
reverts that one string to English, which is exactly the kind of defect that
survives to release. Check the files instead.
"""
import json
import string

import pytest

from app.i18n import LANGUAGES, _locales_dir, available_languages
from tests.test_translation_coverage import _tr_keys

_fmt = string.Formatter()


def _placeholders(text: str):
    """The set of {named} fields in *text*, or None if it will not parse."""
    try:
        return {field for _, field, _, _ in _fmt.parse(text) if field is not None}
    except ValueError:
        return None


def _locale_files():
    out = []
    for name, code in LANGUAGES.items():
        if code == "en":
            continue
        path = _locales_dir() / f"{code}.json"
        if path.exists():
            out.append(pytest.param(path, id=code))
    return out


@pytest.mark.parametrize("path", _locale_files())
def test_translations_keep_every_placeholder(path):
    """A renamed or dropped {field} reverts the string to English at runtime."""
    data = json.loads(path.read_text(encoding="utf-8"))
    problems = []
    for key, value in data.items():
        if key.startswith("__") or not isinstance(value, str):
            continue
        want, got = _placeholders(key), _placeholders(value)
        if want is None:
            problems.append(f"source key does not parse: {key!r}")
        elif got is None:
            problems.append(f"translation does not parse: {value!r}")
        elif want != got:
            problems.append(
                f"{key!r}\n      expects {sorted(want)}, translation has {sorted(got)}"
            )
    assert not problems, (
        f"{path.name}: placeholder mismatches —\n    " + "\n    ".join(problems))


@pytest.mark.parametrize("path", _locale_files())
def test_translations_are_not_left_empty(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    blank = [k for k, v in data.items()
             if not k.startswith("__") and isinstance(v, str) and not v.strip()]
    assert not blank, f"{path.name}: empty translations for {blank}"


def test_shipped_languages_are_substantially_complete():
    """A language in the picker should cover the strings users can reach.

    Locale files retain historical and experimental entries that may no longer
    have a call site. Comparing every locale to the largest such file marks a
    finished locale incomplete for text that cannot render. Measure active
    literal ``tr()`` keys instead, while allowing deliberate technical labels
    such as ``stdout`` to remain untranslated.
    """
    keys = _tr_keys()

    for name in available_languages():
        code = LANGUAGES[name]
        if code == "en":
            continue
        data = json.loads((_locales_dir() / f"{code}.json").read_text(encoding="utf-8"))
        translated = sum(1 for k in keys if data.get(k))
        coverage = translated / len(keys)
        assert coverage >= 0.98, (
            f"{name} covers only {coverage:.1%} of the {len(keys)} known strings "
            f"({len(keys) - translated} missing) — it will render half in English"
        )


@pytest.mark.parametrize("locale", ["uk", "fr"])
def test_locales_keep_the_canonical_podbye_categories_in_english(locale):
    """Category labels are shared taxonomy identifiers, not translated UI copy."""
    data = json.loads((_locales_dir() / f"{locale}.json").read_text(encoding="utf-8"))
    categories = {
        "Images", "Dev Artifacts", "AI / ML", "Applications", "Unknown",
        "Downloads", "System", "Application Data", "Databases", "Cache & Temp",
        "Browser Data", "System Logs", "Archives", "Installers", "Desktop",
        "Documents", "Media",
    }
    assert {key: data.get(key) for key in categories} == {
        key: key for key in categories
    }


def test_ukrainian_findings_names_the_persistent_cleanup_exclusion():
    """Keep is a durable no-cleanup rule, not a save action or hidden filter."""
    data = json.loads((_locales_dir() / "uk.json").read_text(encoding="utf-8"))
    assert data["Keep"] == "Виключити з очищення"
    assert data["Kept"] == "ВИКЛЮЧЕНО"
    assert data["Remove from Keep"] == "Повернути до очищення"
    assert data["Kept paths"] == "Ігноровані шляхи"
    assert "зберег" not in data["Marked with Keep in Findings. Nothing inside these is ever selected by a bulk action, and cleanup refuses them outright."].lower()


def test_ukrainian_startups_uses_windows_startup_terms_not_internal_labels():
    data = json.loads((_locales_dir() / "uk.json").read_text(encoding="utf-8"))
    assert data["STARTUP INSPECTION"] == "ДЕТАЛІ АВТОЗАПУСКУ"
    assert data["BOOT IMPACT"] == "ВПЛИВ НА ЗАПУСК"
    assert data["need review or are protected"] == (
        "потребують перевірки або захищені")
    assert data["User startup registry"] == (
        "Реєстр автозапуску поточного користувача")
    assert data["Scheduled task (logon)"] == (
        "Заплановане завдання (вхід у систему)")
    # This is a recommendation reason for a Windows/vendor-owned entry, not
    # Findings' Protected state: Startups cannot change Windows configuration.
    assert data["SYSTEM-MANAGED"] == "КЕРУЄТЬСЯ СИСТЕМОЮ"


def test_ukrainian_copy_names_the_actual_cleanup_and_startup_actions():
    """Safety copy must not call a Recycle Bin move a permanent deletion.

    The generic Optional risk also has a different practical meaning on the
    Startups screen: users can start the program manually instead.
    """
    data = json.loads((_locales_dir() / "uk.json").read_text(encoding="utf-8"))
    assert data["Quick Cleanup"] == "Швидке очищення"
    assert data["items removed"] == "елементів прибрано з диска"
    assert data["Optional at startup"] == "Необов’язково під час запуску"
    assert data["OPTIONAL AT STARTUP"] == "НЕОБОВ’ЯЗКОВО ПІД ЧАС ЗАПУСКУ"
    assert "жодна активна операція не переривається" in data[
        "The system-wide Temp folder used by Windows services, background tasks, and installers. Clearing it frees space left behind after updates and software installs. Files locked by a running process are automatically skipped — no active operations are interrupted."
    ]
