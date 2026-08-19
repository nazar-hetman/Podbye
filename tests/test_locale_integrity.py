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
    """A language in the picker should not be mostly English.

    French shipped at 984 of 1,073 strings — the AI status labels, several
    dialog help texts and most feed captions fell back to English mid-sentence.
    Guard the floor rather than demanding a perfect 100%, so adding a string
    does not immediately break the build.
    """
    reference = max(
        (json.loads((_locales_dir() / f"{code}.json").read_text(encoding="utf-8"))
         for name, code in LANGUAGES.items()
         if code != "en" and (_locales_dir() / f"{code}.json").exists()),
        key=len,
    )
    keys = [k for k in reference if not k.startswith("__")]

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
