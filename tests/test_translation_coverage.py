"""Every user-facing string must be translatable, and stay translated.

Two failures look identical to a user but have different causes:
  1. the string never reaches tr()          -> untranslatable in EVERY language
  2. it reaches tr() but the locale lacks it -> falls back to English

A third is invisible to tooling: tr(variable) cannot be discovered by any
static scan, so those tables are checked explicitly below.
"""
import ast
import io
import json
import pathlib

import pytest

APP = pathlib.Path(__file__).resolve().parents[1] / "app"
LOCALES = APP / "locales"


def _tr_keys() -> set[str]:
    """Every literal string passed to tr() anywhere in the app."""
    keys: set[str] = set()
    for path in APP.rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and getattr(node.func, "id", "") == "tr"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                keys.add(node.args[0].value)
    return keys


def _table(code: str) -> dict:
    with io.open(LOCALES / f"{code}.json", encoding="utf-8") as fh:
        return {k: v for k, v in json.load(fh).items() if not k.startswith("__")}


def _rule_strings() -> set[str]:
    """Cleanup rule text, reached as tr(rule["intro"]) — invisible to a scan."""
    from app.services import cleanup_result_classifier as c
    out: set[str] = set()
    for rule in list(c._EXPECTED_RULES.values()) + [c._fallback_expected_rule()]:
        out.add(rule["intro"])
        out.add(rule["context"])
        out.update(rule["actions"])
    return out


def test_ukrainian_is_complete():
    """Ukrainian is the reference locale — it must not regress."""
    missing = sorted(k for k in _tr_keys() if not _table("uk").get(k))
    assert not missing, (
        f"{len(missing)} untranslated Ukrainian strings, e.g. {missing[:5]}")


@pytest.mark.parametrize("code", ["uk", "fr"])
def test_cleanup_rule_text_is_translated(code):
    """These are the explanations shown after a cleanup. They reach tr() via a
    variable, so no static check would ever notice them going missing."""
    table = _table(code)
    missing = sorted(k for k in _rule_strings() if not table.get(k))
    assert not missing, f"{code}: untranslated cleanup rule text: {missing[:3]}"


@pytest.mark.parametrize("code", ["uk", "fr"])
def test_destructive_action_strings_are_translated(code):
    """A user must never be asked to confirm deleting files in a language they
    did not choose. These are the strings attached to irreversible actions."""
    critical = [
        "Move to Recycle Bin", "Permanently delete files", "Confirm Cleanup",
        "Deep Uninstall", "Safe", "Optional", "Review", "Protected",
        "Cancel", "Recycle Bin",
    ]
    table = _table(code)
    missing = [k for k in critical if not table.get(k)]
    assert not missing, f"{code}: untranslated destructive-action text: {missing}"


@pytest.mark.parametrize("code", ["uk", "fr"])
def test_placeholders_survive_translation(code):
    """A translation that drops or renames {a placeholder} raises at format
    time — or worse, silently prints the wrong number."""
    import re
    table = _table(code)
    bad = []
    for key, value in table.items():
        want = set(re.findall(r"\{(\w+)", key))
        got = set(re.findall(r"\{(\w+)", value))
        if want != got:
            bad.append(f"{key[:40]!r}: expected {sorted(want)}, got {sorted(got)}")
    assert not bad, f"{code}: placeholder mismatch —\n  " + "\n  ".join(bad[:5])


def test_french_covers_the_core_ui():
    """French is partial by design (prose falls back to English), but the
    everyday UI must be translated."""
    core = [
        "Home", "Analyze", "Findings", "Settings", "History", "Startups",
        "Start scan", "Stop scan", "Ask AI", "Move to Recycle Bin",
        "Safe", "Review", "Protected", "Cancel", "Close", "Apply",
    ]
    missing = [k for k in core if not _table("fr").get(k)]
    assert not missing, f"French core UI untranslated: {missing}"
