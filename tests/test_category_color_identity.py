"""A category must keep its own colour — through normalisation and theme switches.

Two bugs found together in the beta bench, with the same visible symptom (a
category drawn in the wrong colour) and two entirely different causes:

  1. The dashboard normalised category names with ``.title()`` before
     grouping. That rewrites "AI / ML" to "Ai / Ml", which matches no key in
     _CATEGORY_COLORS and no parent, so the category fell through to the
     "Other" swatch and its own colour was never drawn anywhere. In the Black
     theme "Other" is #1c1c1c on a #101010 panel — invisible.

  2. The donut baked each wedge's hex into its segment list at set_data()
     time. A theme switch repainted the chart with the stale palette, so the
     wedges stayed the same colour in all four themes while the panel behind
     them changed. In Paper the largest wedge became light grey on cream.

Both are invisible to a test that only ever looks at one theme in English.
"""
import pytest

from app.themes.theme_manager import (
    THEME_KEYS, build_qss, get_category_colors,
)


@pytest.fixture
def app(qapp):
    from app.fonts import load_fonts
    load_fonts()
    yield qapp
    build_qss("forest")


# ── 1. Normalisation must preserve registered names ──────────────

def test_every_registered_category_survives_normalisation(app):
    """Normalising a category must not invent a name the palette lacks."""
    from app.screens.findings_dashboard import canonical_category

    for name in get_category_colors("forest"):
        assert canonical_category(name) == name, (
            f"normalising {name!r} produced {canonical_category(name)!r}, "
            f"which has no colour of its own"
        )


def test_acronym_categories_keep_their_capitalisation(app):
    """The case .title() got wrong, stated explicitly."""
    from app.screens.findings_dashboard import canonical_category

    assert canonical_category("AI / ML") == "AI / ML"
    assert canonical_category("ai / ml") == "AI / ML"
    assert canonical_category("  AI / ML  ") == "AI / ML"


def test_normalisation_still_tidies_unregistered_names(app):
    from app.screens.findings_dashboard import canonical_category

    assert canonical_category("weird custom thing") == "Weird Custom Thing"
    assert canonical_category("unknown") == "Unknown"
    assert canonical_category("") == "Unknown"
    assert canonical_category(None) == "Unknown"


def test_no_registered_category_resolves_to_the_other_swatch(app):
    """The symptom users saw: a real category wearing the fallback colour."""
    from app.screens.findings_dashboard import (
        _get_category_color, canonical_category,
    )

    for theme in THEME_KEYS:
        build_qss(theme)
        other = get_category_colors(theme)["Other"]
        for name in get_category_colors(theme):
            if name == "Other":
                continue
            resolved = _get_category_color(canonical_category(name))
            assert resolved != other, (
                f"{theme}: {name!r} falls through to the Other swatch"
            )


# ── 2. The donut must follow the theme ───────────────────────────

def _donut_with_data(app):
    from app.screens.findings_dashboard import DonutChartWidget

    d = DonutChartWidget()
    cats = [
        ("AI / ML", {"size_bytes": 40 * 10 ** 9, "percentage": 50.0}),
        ("Media",   {"size_bytes": 30 * 10 ** 9, "percentage": 37.5}),
        ("Unknown", {"size_bytes": 10 * 10 ** 9, "percentage": 12.5}),
    ]
    d.set_data(cats, sum(c["size_bytes"] for _, c in cats))
    return d


def test_donut_resolves_wedge_colours_from_the_live_palette(app):
    """The chart must not cache a hex that a theme switch invalidates."""
    from app.screens.findings_dashboard import _get_category_color

    d = _donut_with_data(app)
    seen = {}
    for theme in THEME_KEYS:
        build_qss(theme)
        seen[theme] = [_get_category_color(s["color_key"]) for s in d._segments]

    # Every theme defines its own hues; if the chart were caching, these lists
    # would be identical across themes — which is exactly what the bench saw.
    assert len(set(map(tuple, seen.values()))) > 1, (
        "wedge colours are identical in every theme — the palette is stale"
    )
    d.deleteLater()


def test_the_painted_wedge_is_the_theme_colour(app):
    """End-to-end, on the pixels themselves.

    Comparing whole images would be a weaker check: the centre text also
    recolours with the palette, so two renders differ even when every wedge
    stayed stale. Sample inside the ring instead, and require the exact hue
    the active theme registers for that category.
    """
    import math

    SIZE = 240
    d = _donut_with_data(app)
    d.resize(SIZE, SIZE)

    # First wedge starts at 12 o'clock and sweeps clockwise; 20° into it is
    # clear of the separator gaps at either end.
    centre = SIZE // 2
    radius = (SIZE - 14 * 2) / 2 * 0.85      # inside the ring, outside the hole
    angle = math.radians(20)
    x = int(centre + radius * math.sin(angle))
    y = int(centre - radius * math.cos(angle))

    for theme in THEME_KEYS:
        build_qss(theme)
        painted = d.grab().toImage().pixelColor(x, y).name()
        expected = get_category_colors(theme)["AI / ML"]
        assert painted == expected, (
            f"{theme}: wedge painted {painted}, but this theme registers "
            f"{expected} for AI / ML"
        )
    d.deleteLater()


def test_pooled_other_wedge_resolves_in_a_translated_ui(app):
    """The pooled wedge is *labelled* in the user's language.

    Its colour must still be looked up by the untranslated key, or every
    non-English locale loses the Other colour the way AI/ML lost its own.
    """
    from app.i18n import get_language, set_language
    from app.screens.findings_dashboard import (
        DonutChartWidget, _get_category_color,
    )

    original = get_language()
    try:
        set_language("Ukrainian")
        build_qss("forest")
        d = DonutChartWidget()
        cats = [(f"cat{i}", {"size_bytes": 10 ** 9, "percentage": 5.0})
                for i in range(DonutChartWidget.MAX_SLICES + 4)]
        d.set_data(cats, sum(c["size_bytes"] for _, c in cats))

        pooled = [s for s in d._segments if s.get("is_other")]
        assert pooled, "expected the tail to be pooled into an Other wedge"
        assert pooled[0]["color_key"] == "Other", (
            "the pooled wedge looks its colour up by its translated label"
        )
        assert _get_category_color(pooled[0]["color_key"]) == \
            get_category_colors("forest")["Other"]
        d.deleteLater()
    finally:
        set_language(original)
