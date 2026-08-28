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


def _delta_e(a, b):
    """CIE76 distance, enough to say whether two swatches read as one colour."""
    def lab(hexs):
        h = hexs.lstrip("#")
        rgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        lin = [(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
               for c in rgb]
        r, g, bl = lin
        x = r * 0.4124 + g * 0.3576 + bl * 0.1805
        y = r * 0.2126 + g * 0.7152 + bl * 0.0722
        z = r * 0.0193 + g * 0.1192 + bl * 0.9505

        def f(t):
            return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

        fx, fy, fz = f(x / 0.95047), f(y), f(z / 1.08883)
        return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))

    return sum((u - v) ** 2 for u, v in zip(lab(a), lab(b))) ** 0.5


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


def test_the_dark_palette_gives_every_category_its_own_colour():
    """Nine colours cannot label nineteen categories, and neither can
    seventeen. Protected / Restricted repeated Media and Unknown repeated
    Documents, so a row could agree with its own donut segment and still be
    indistinguishable from a different category's pair."""
    palette = get_category_colors("forest")
    duplicates = {v for v in palette.values()
                  if list(palette.values()).count(v) > 1}
    assert not duplicates, f"colours shared by several categories: {duplicates}"


def test_the_separated_categories_are_far_enough_apart():
    """Judged against the palette's own bar: its tightest shipping pair is
    Applications / Media at dE 18."""
    palette = get_category_colors("forest")
    for a, b in (("Media", "Protected / Restricted"), ("Documents", "Unknown")):
        assert _delta_e(palette[a], palette[b]) >= 18.0, f"{a} and {b} are too close"


def test_paper_still_carries_the_same_two_collisions():
    """Deliberately pinned, not tolerated. The separation was scoped to the
    dark themes; Paper has the identical defect and this fails the moment it
    is fixed, so the pin gets removed rather than forgotten."""
    palette = get_category_colors("paper")
    assert palette["Media"] == palette["Protected / Restricted"]
    assert palette["Documents"] == palette["Unknown"]


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
