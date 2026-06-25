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
    assert len(_KNOWN_MONOLITH_PATTERNS) >= 36
    assert len(_MONOLITH_DISPLAY_NAMES) >= 36
    assert len(_MONOLITH_ENTITY_TYPES) >= 19


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
