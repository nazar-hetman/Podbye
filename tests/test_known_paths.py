"""Curated rules for well-known Windows storage locations.

Rules annotate; they never contradict better evidence, and they never make an
item look safer than it is.
"""
import os

import pytest

from app.services import known_paths as kp
from app.services.known_paths import apply_known_path_rules, lookup
from app.models.smart_entity import SmartEntity

MB = 1024 * 1024


def _e(path, etype="unknown_folder", risk="Review", name=None):
    return SmartEntity(path=path, name=name or os.path.basename(path),
                       entity_type=etype, size_bytes=MB, file_count=5,
                       folder_count=1, risk=risk)


@pytest.fixture(autouse=True)
def _fresh_rules(monkeypatch):
    monkeypatch.setattr(kp, "_CACHE", None)


def _local(*parts):
    return os.path.join(os.environ.get("LOCALAPPDATA", "C:/x"), *parts) \
        .replace("\\", "/")


# ── the rules fire ───────────────────────────────────────────────


def test_thumbnail_cache_is_recognised_not_called_a_database():
    """787 MB of thumbcache_*.db showed as "Likely database ..." at Review,
    hiding easily reclaimable space and making it sound precious."""
    e = _e(_local("Microsoft", "Windows", "Explorer"),
           etype="database", risk="Review", name="Likely database for Microsoft")
    assert apply_known_path_rules([e]) == 1
    assert e.entity_type == "cache_folder"
    assert e.risk == "Safe"
    assert "thumbnail" in e.name.lower() or "icon" in e.name.lower()
    assert "rebuilt" in e.risk_reason.lower()


def test_package_cache_is_optional_not_safe():
    """Content analysis calls it a cache, but removing it costs offline repair,
    so the rule raises caution rather than lowering it."""
    e = _e(os.path.join(os.environ.get("ProgramData", "C:/pd"), "Package Cache")
           .replace("\\", "/"), etype="cache_folder", risk="Safe")
    apply_known_path_rules([e])
    assert e.risk == "Optional"
    assert "repair" in e.risk_reason.lower()


def test_windows_installed_vendor_folder_is_protected():
    """The reported case: Program Files (x86)\\Microsoft holds Edge/Copilot,
    which Windows installed on its own."""
    e = _e("C:/Program Files (x86)/Microsoft", etype="application", risk="Review")
    apply_known_path_rules([e])
    assert e.risk == "Protected"
    assert "windows" in e.risk_reason.lower()


# ── precedence ───────────────────────────────────────────────────


def test_a_rule_never_lowers_caution_on_a_specific_classification():
    """A pass that identified something specific keeps its answer unless the
    rule is more cautious."""
    e = _e(_local("Microsoft", "Windows", "Explorer"),
           etype="game_saves", risk="Protected")
    apply_known_path_rules([e])
    assert e.risk == "Protected", "curated rule downgraded a protected item"
    assert e.entity_type == "game_saves"


def test_a_rule_may_always_raise_caution():
    e = _e("C:/Program Files/Microsoft", etype="application", risk="Safe")
    apply_known_path_rules([e])
    assert e.risk == "Protected"


def test_unlisted_paths_are_untouched():
    e = _e("C:/Some/Random/Folder", etype="unknown_folder", risk="Review")
    assert apply_known_path_rules([e]) == 0
    assert e.entity_type == "unknown_folder"


def test_child_paths_do_not_inherit_a_rule():
    """Rules match a folder exactly; a subfolder is not automatically the same
    thing (Explorer/Something is not the thumbnail cache itself)."""
    e = _e(_local("Microsoft", "Windows", "Explorer", "Subfolder"))
    assert apply_known_path_rules([e]) == 0


# ── table sanity ─────────────────────────────────────────────────


def test_every_rule_is_complete_and_uses_a_known_risk():
    from app.models.smart_entity import ENTITY_TYPES
    valid = {"Safe", "Optional", "Review", "Protected"}
    for rule in kp.rules():
        assert rule["path"] and not rule["path"].endswith("/")
        assert rule["type"] in ENTITY_TYPES, rule["type"]
        assert rule["risk"] in valid, rule
        assert rule["name"] and rule["reason"]


def test_no_rule_claims_something_is_deletable_without_explaining():
    for rule in kp.rules():
        if rule["risk"] == "Safe":
            assert len(rule["reason"]) > 20, (
                f"{rule['name']}: a Safe verdict needs a real explanation")


def test_lookup_is_case_and_separator_insensitive():
    path = _local("Microsoft", "Windows", "Explorer")
    assert lookup(path.replace("/", "\\").upper()) is not None
