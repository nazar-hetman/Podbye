"""A category's colour is data, not decoration.

It is what ties a row marker to its slice of the donut, so it has to survive
the theme the way a key on a chart does.

Black used to carry its own greyscale ramp. That broke the mapping twice over:
the donut went grey while the row markers stayed coloured, and the ramp held
nine distinct greys for nineteen categories, so Applications, Media and
Protected / Restricted were all #dedede — three slices the eye could not tell
apart.
"""
import pytest

from app.screens.findings_dashboard import _get_category_color, canonical_category
from app.themes import theme_manager as tm
from app.themes.theme_manager import _CATEGORY_COLORS, THEME_KEYS, get_category_colors

_THEMES = ["forest", "amber", "mono", "paper"]


def _is_grey(hex_color):
    h = hex_color.lstrip("#")
    return h[0:2].lower() == h[2:4].lower() == h[4:6].lower()


# ── colours stay colours ──────────────────────────────────────────

@pytest.mark.parametrize("theme", _THEMES)
def test_no_theme_renders_the_category_palette_in_grey(theme):
    """The one exception is a deliberately neutral "Other"."""
    palette = get_category_colors(theme)
    greys = {k: v for k, v in palette.items() if _is_grey(v) and k != "Other"}
    assert not greys, f"{theme}: {len(greys)} category colours are greyscale: {greys}"


@pytest.mark.parametrize("theme", _THEMES)
def test_categories_are_distinguishable_from_one_another(theme):
    """Nine colours cannot label nineteen categories."""
    palette = get_category_colors(theme)
    distinct = len(set(palette.values()))
    assert distinct >= len(palette) - 2, (
        f"{theme}: only {distinct} distinct colours for {len(palette)} categories")


def test_black_no_longer_collapses_three_categories_into_one():
    """The exact collision that was reported."""
    palette = get_category_colors("mono")
    trio = {palette[k] for k in ("Applications", "Media", "Protected / Restricted")}
    assert len(trio) > 1


# ── the marker and the slice read the same table ──────────────────

@pytest.mark.parametrize("theme", _THEMES)
@pytest.mark.parametrize("category", [
    "Applications", "Media", "Cache & Temp", "AI / ML", "Browser Data", "Games",
])
def test_a_category_has_one_colour_per_theme(theme, category):
    """Row marker and donut segment both resolve through _get_category_color,
    so this is the single value both of them draw."""
    tm._current_theme_key = theme
    colour = _get_category_color(category)
    assert colour == get_category_colors(theme)[category]


@pytest.mark.parametrize("category", ["Applications", "Media", "AI / ML"])
def test_black_agrees_with_the_other_dark_themes(category):
    """Semantic identity does not change because the chrome went monochrome."""
    assert get_category_colors("mono")[category] == get_category_colors("forest")[category]


# ── the dark themes share one table, Paper keeps its own ──────────

def test_the_dark_themes_share_a_single_palette_object():
    """A second copy is a second thing to forget to update — which is how
    Black drifted in the first place."""
    assert get_category_colors("forest") is get_category_colors("amber")
    assert get_category_colors("mono") is get_category_colors("forest")


def test_paper_keeps_its_own_inks():
    """A light surface needs deeper, more saturated colours."""
    assert get_category_colors("paper") is not get_category_colors("forest")
    assert get_category_colors("paper") != get_category_colors("forest")


def test_every_theme_still_covers_every_category():
    reference = set(_CATEGORY_COLORS["forest"])
    for theme in THEME_KEYS:
        assert set(get_category_colors(theme)) == reference, f"{theme} diverged"


# ── routing that used to be silently broken ───────────────────────

@pytest.mark.parametrize("theme", _THEMES)
def test_an_odd_spelling_still_finds_its_colour(theme):
    """"ai / ml" title-cased to "Ai / Ml", matched nothing, and fell through
    to the Other swatch."""
    tm._current_theme_key = theme
    resolved = canonical_category("ai / ml")
    assert resolved == "AI / ML"
    assert _get_category_color(resolved) == get_category_colors(theme)["AI / ML"]


@pytest.mark.parametrize("theme", _THEMES)
def test_an_unregistered_category_falls_back_without_raising(theme):
    tm._current_theme_key = theme
    assert _get_category_color("Nonesuch") == get_category_colors(theme)["Other"]


# ── Black keeps its monochrome chrome ─────────────────────────────

def test_black_chrome_is_still_monochrome():
    """The fix is scoped to data colours; surfaces, borders and text stay grey."""
    p = tm.PALETTES["mono"]
    for key in ("bg", "bg_deep", "panel", "panel_alt", "panel_hover",
                "border", "border_alt", "text", "text_dim", "text_faint", "accent"):
        assert _is_grey(p[key]), f"mono.{key} = {p[key]} is not monochrome"
