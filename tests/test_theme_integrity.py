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


# ── surfaces must be visible against their background ────────────

def _relative_luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    channels = []
    for i in (0, 2, 4):
        v = int(h[i:i + 2], 16) / 255
        channels.append(v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: str, b: str) -> float:
    la, lb = _relative_luminance(a), _relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def test_cards_are_distinguishable_from_the_page():
    """A card whose surface matches the page is not a card.

    Black sat at 1.053 — the flattest of the four, and the only theme with no
    hue difference to carry the edge, so it was also the one where it showed.
    The floor is below every current theme but above where Black and Amber were.
    """
    for name, p in PALETTES.items():
        ratio = _contrast(p["bg"], p["panel"])
        assert ratio >= 1.065, (
            f"theme '{name}': panel {p['panel']} is only {ratio:.3f} against "
            f"bg {p['bg']} — the card edge disappears")


def test_the_raised_surface_is_actually_raised():
    """PanelAlt has to differ from Panel or the distinction buys nothing."""
    for name, p in PALETTES.items():
        assert p["panel_alt"] != p["panel"], (
            f"theme '{name}': panel_alt is identical to panel")


# ── risk tiers must not impersonate the accent ───────────────────

def test_no_caution_tier_collides_with_the_accent():
    """Amber shipped review == accent == #d79c54.

    The accent is the colour of a CTA, a selected row and a key figure. A tier
    that asks the user to *stop and judge* must not wear it: "this needs your
    attention" rendered identically to "click here" is the one confusion a
    cleanup tool cannot afford.

    'safe' is deliberately exempt. Forest sets safe == accent, and there the
    two agree rather than conflict — the accent marks the recommended action
    and Safe is the tier that action applies to. Only the tiers that mean
    "not simply go ahead" are held apart from it.
    """
    for name, p in PALETTES.items():
        for tier in ("review", "risk", "optional"):
            assert p[tier] != p["accent"], (
                f"theme '{name}': caution tier '{tier}' is the accent colour {p['accent']}")


def test_risk_tiers_are_distinct_from_each_other():
    for name, p in PALETTES.items():
        tiers = {t: p[t] for t in ("safe", "review", "risk", "optional")}
        assert len(set(tiers.values())) == len(tiers), (
            f"theme '{name}': two risk tiers share a colour — {tiers}")


def test_caution_tints_do_not_collide_with_the_selection_tint():
    """The soft variants back the same badges; same argument, same exemption."""
    for name, p in PALETTES.items():
        for tier in ("review_soft", "risk_soft", "optional_soft"):
            assert p[tier] != p["accent_soft"], (
                f"theme '{name}': '{tier}' is the selection tint {p['accent_soft']}")
