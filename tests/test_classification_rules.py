"""Golden tests for the externalized classification rule tables.

These guard the JSON rule file and its reconstruction in entity_detector,
so an accidental rule loss or shape regression is caught immediately.
"""
import json

from app.services.entity_detector import (
    _RULES_PATH,
    _APP_MARKERS,
    _DIR_ENTITY_MAP,
    _CACHE_SOURCE_HINTS,
    _KNOWN_MONOLITH_PATTERNS,
    _MONOLITH_DISPLAY_NAMES,
    _MONOLITH_ENTITY_TYPES,
)

_EXPECTED_KEYS = {
    "app_markers", "dir_entity_map", "cache_source_hints",
    "monolith_patterns", "monolith_display_names", "monolith_entity_types",
    # Relative paths inside an entity that name a component in the inspector
    # ("steamapps/common" -> Installed games). See app/models/entity_contents.
    "component_rules",
}


def test_rules_file_exists_and_loads():
    assert _RULES_PATH.exists(), f"missing rules file: {_RULES_PATH}"
    data = json.loads(_RULES_PATH.read_text(encoding="utf-8"))
    assert set(data) == _EXPECTED_KEYS


def test_table_baseline_sizes():
    # Baselines captured at externalization. Tables may grow; they must not
    # silently shrink or empty out.
    assert len(_APP_MARKERS) >= 69
    assert len(_DIR_ENTITY_MAP) >= 116
    assert len(_CACHE_SOURCE_HINTS) >= 150
    # 36/36/19 at externalization, less one: "r-" was withdrawn. Two
    # characters matched by prefix, so it claimed every folder whose name
    # began "r-" — r-projects, r-and-d-2024 — and typed each one Safe with a
    # whole-folder recycle, contained so nothing inside could correct it.
    # See tests/test_a_name_is_not_permission_to_delete.py.
    assert len(_KNOWN_MONOLITH_PATTERNS) >= 35
    assert len(_MONOLITH_DISPLAY_NAMES) >= 35
    assert len(_MONOLITH_ENTITY_TYPES) >= 18

    from app.models.entity_contents import _rules
    assert len(_rules()) >= 23


def test_a_component_rule_is_never_a_single_segment():
    """A bare "userdata" means Steam's cloud saves under Steam and nothing in
    particular anywhere else. Rules match relative to an entity's own folder,
    so a one-segment rule would fire on the wrong app."""
    from app.models.entity_contents import _rules
    shallow = [path for path, _name in _rules() if "/" not in path]
    assert not shallow, shallow


def test_app_markers_shape():
    for key, value in _APP_MARKERS.items():
        assert isinstance(value, tuple) and len(value) == 2, key
        entity_type, display_name = value
        assert isinstance(entity_type, str) and entity_type
        assert display_name is None or isinstance(display_name, str)


def test_monolith_patterns_is_ordered_tuple():
    # _monolith_display / _monolith_type rely on first-match-wins iteration.
    assert isinstance(_KNOWN_MONOLITH_PATTERNS, tuple)
    assert all(isinstance(p, str) and p for p in _KNOWN_MONOLITH_PATTERNS)
