"""Theme integrity guards.

These lock in the "every theme is self-contained" invariant: a missing palette
key would silently fall back to another theme's hardcoded colour, which is
exactly the cross-theme leakage we want to prevent.
"""
import re

from app.themes.theme_manager import (
    PALETTES, THEME_KEYS, build_qss,
    get_category_colors, _CATEGORY_COLORS,
)


def test_all_palettes_share_identical_keys():
    key_sets = {name: frozenset(p) for name, p in PALETTES.items()}
    reference = key_sets["forest"]
    for name, keys in key_sets.items():
        assert keys == reference, (
            f"theme '{name}' key set differs: "
            f"missing={reference - keys}, extra={keys - reference}"
        )


def test_on_accent_present_everywhere():
    for name, p in PALETTES.items():
        assert p.get("on_accent"), f"theme '{name}' is missing 'on_accent'"


def test_qss_compiles_for_every_theme():
    # build_qss does _BASE_QSS.format(**palette); a missing key raises KeyError.
    for key in THEME_KEYS:
        qss = build_qss(key)
        assert isinstance(qss, str) and len(qss) > 1000


def test_qss_has_no_unresolved_placeholders():
    for key in THEME_KEYS:
        qss = build_qss(key)
        # No leftover single-brace placeholders like {accent} after formatting.
        leftovers = re.findall(r"(?<!\{)\{[a-z_]+\}(?!\})", qss)
        assert not leftovers, f"theme '{key}' has unresolved placeholders: {leftovers}"


def test_category_colors_complete_per_theme():
    # Every theme must define the same category-colour keys.
    ref = frozenset(_CATEGORY_COLORS["forest"])
    for name in THEME_KEYS:
        keys = frozenset(get_category_colors(name))
        assert keys == ref, (
            f"category colours for '{name}' differ: "
            f"missing={ref - keys}, extra={keys - ref}"
        )


def test_palette_values_are_hex_colors():
    color_like = re.compile(r"^#[0-9a-fA-F]{6}$")
    for name, p in PALETTES.items():
        for key, val in p.items():
            if key.endswith("_font"):
                continue
            assert color_like.match(val), f"{name}.{key} = {val!r} is not a hex colour"
