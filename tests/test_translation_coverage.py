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


def test_no_new_untranslatable_ui_strings():
    """A literal passed straight to a UI sink can never be translated, in any
    language. Only the product wordmark is allowed to stay hardcoded."""
    import re

    SINKS = ("setText", "setToolTip", "setPlaceholderText", "setWindowTitle",
             "QLabel", "QPushButton", "QCheckBox", "QRadioButton")
    SKIP = re.compile(
        r"^[\s\W\d_]*$|^[a-z_]+$|^\{[^}]*\}$"
        r"|font|color|border|background|padding|margin|px|rgba|#[0-9a-fA-F]{3,8}"
        r"|^https?:|\|/|\.json$|\.log$|\.db$|^%|^C:", re.IGNORECASE)
    ALLOWED = {"VIGIL", "VIGIL · LOCAL SYSTEM ANALYSIS"}

    offenders = []
    for path in APP.rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = getattr(node.func, "attr", "") or getattr(node.func, "id", "")
            if fn not in SINKS:
                continue
            for arg in node.args:
                text = None
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    text = arg.value.strip()
                elif isinstance(arg, ast.JoinedStr):
                    lit = "".join(v.value for v in arg.values
                                  if isinstance(v, ast.Constant)
                                  and isinstance(v.value, str)).strip()
                    text = lit if len(lit) >= 8 else None
                if not text or len(text) < 3 or SKIP.search(text) or text in ALLOWED:
                    continue
                offenders.append(f"{path.name}:{node.lineno} {text[:50]!r}")
    assert not offenders, (
        "user-facing text not wrapped in tr() —\n  " + "\n  ".join(offenders))


# ── strings reached through tr(variable) ─────────────────────────
# These live in lookup tables, so no static scan finds them — and they are the
# most visible text in Findings. They were entirely untranslated (entity type
# labels 0/40 in BOTH languages) while the audit still reported "100%".


def _dynamic_tables() -> dict[str, set[str]]:
    import app.screens.quick_cleanup as qc
    import app.screens.settings as st
    from app.models.findings_table_model import _HEADER_KEYS
    from app.models.smart_entity import ENTITY_TYPES, _CATEGORY_BY_TYPE

    tables = {
        "quick cleanup explanations":
            set(qc._EXPLANATIONS.values()) | {qc._EXPLANATION_FALLBACK},
        "categories": set(_CATEGORY_BY_TYPE.values()),
        "entity type labels": set(ENTITY_TYPES.values()),
        "table headers": {k for k in _HEADER_KEYS if k},
    }
    subs = getattr(st, "_SECTION_SUBS", None)
    if isinstance(subs, dict):
        tables["settings sections"] = set(subs.values())
    return tables


@pytest.mark.parametrize("code", ["uk", "fr"])
def test_dynamic_lookup_tables_are_fully_translated(code):
    table = _table(code)
    missing = {}
    for name, keys in _dynamic_tables().items():
        gap = sorted(k for k in keys if not table.get(k))
        if gap:
            missing[name] = gap
    assert not missing, (
        f"{code}: lookup-table text with no translation — "
        + "; ".join(f"{n} ({len(v)}): {v[0][:40]!r}" for n, v in missing.items()))


def test_every_entity_type_label_is_translatable():
    """The TYPE field in the inspector renders one of these for every entity."""
    from app.models.smart_entity import ENTITY_TYPES
    for code in ("uk", "fr"):
        table = _table(code)
        missing = [v for v in ENTITY_TYPES.values() if not table.get(v)]
        assert not missing, f"{code}: untranslated entity types: {missing[:4]}"
