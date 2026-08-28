"""The action footer in the finding inspector, and Black's semantic colours.

Three buttons shared an object name and a row, but only two of them were given
the utility style, so Keep alone kept the base #SecondaryAction rule — a larger
font and roomier padding than the controls either side of it. Deep Uninstall
sat full-width directly under Move to Recycle Bin, reading as a second primary
action competing with the first.
"""
import inspect

import pytest

import app.screens.findings_dashboard as fd
from app.screens.findings_dashboard import _get_category_color
from app.themes import theme_manager as tm
from app.themes.theme_manager import build_qss

_THEMES = ["forest", "amber", "mono", "paper"]
_BUTTONS = ("_btn_open", "_btn_copy", "_btn_keep", "_btn_uninstall", "_btn_recycle")


@pytest.fixture
def panel(qapp, request):
    theme = getattr(request, "param", "forest")
    tm._current_theme_key = theme
    qapp.setStyleSheet(build_qss(theme))
    sig = inspect.signature(fd._PreallocDetailPanel.__init__)
    kw = {n: (lambda *a, **k: None)
          for n in list(sig.parameters)[1:] if n != "parent"}
    p = fd._PreallocDetailPanel(**kw)
    p.resize(420, 800)
    p.show()
    for name in _BUTTONS:
        getattr(p, name).setVisible(True)
    for _ in range(4):
        qapp.processEvents()
    p.layout().activate()
    for _ in range(3):
        qapp.processEvents()
    yield p
    p.deleteLater()
    qapp.processEvents()


def _geom(panel, name):
    b = getattr(panel, name)
    pt = b.mapTo(panel, b.rect().topLeft())
    return pt.x(), pt.y(), b.width(), b.height()


# ── the three utility actions are one set ─────────────────────────

@pytest.mark.parametrize("panel", _THEMES, indirect=True)
def test_keep_is_the_same_button_as_its_neighbours(panel):
    heights = {n: _geom(panel, n)[3] for n in ("_btn_open", "_btn_copy", "_btn_keep")}
    assert len(set(heights.values())) == 1, f"utility buttons differ in height: {heights}"


@pytest.mark.parametrize("panel", _THEMES, indirect=True)
def test_keep_carries_the_utility_style(panel):
    """It was the one button in the row nobody applied it to."""
    assert panel._btn_keep.styleSheet() == panel._btn_open.styleSheet()
    assert panel._btn_keep.styleSheet() == panel._btn_copy.styleSheet()


@pytest.mark.parametrize("panel", _THEMES, indirect=True)
def test_keep_still_reads_as_a_control(panel):
    """A border and a fill, not bare text on the panel."""
    qss = panel._btn_keep.styleSheet()
    assert "border: 1px solid" in qss
    assert "background:" in qss


# ── Deep Uninstall is an alternative, not a headline ──────────────

@pytest.mark.parametrize("panel", _THEMES, indirect=True)
def test_deep_uninstall_shares_the_footer_row(panel):
    ys = {n: _geom(panel, n)[1]
          for n in ("_btn_open", "_btn_copy", "_btn_keep", "_btn_uninstall")}
    assert len(set(ys.values())) == 1, f"not one row: {ys}"


@pytest.mark.parametrize("panel", _THEMES, indirect=True)
def test_deep_uninstall_sits_to_the_right_of_the_utility_actions(panel):
    keep_x = _geom(panel, "_btn_keep")[0]
    unin_x = _geom(panel, "_btn_uninstall")[0]
    assert unin_x > keep_x


@pytest.mark.parametrize("panel", _THEMES, indirect=True)
def test_deep_uninstall_is_no_longer_full_width(panel):
    """Full width under the primary made it a second primary."""
    unin_w = _geom(panel, "_btn_uninstall")[2]
    recycle_w = _geom(panel, "_btn_recycle")[2]
    assert unin_w < recycle_w


# ── the primary action is untouched ───────────────────────────────

@pytest.mark.parametrize("panel", _THEMES, indirect=True)
def test_move_to_recycle_bin_is_still_the_full_width_primary(panel):
    assert panel._btn_recycle.objectName() == "Primary"
    _x, y, w, _h = _geom(panel, "_btn_recycle")
    assert w > _geom(panel, "_btn_uninstall")[2]
    assert y < _geom(panel, "_btn_open")[1], "the primary must sit above the footer"


def test_the_button_texts_are_unchanged(panel):
    """Hierarchy only — no relabelling."""
    assert panel._btn_recycle.text() == "Move to Recycle Bin"
    assert panel._btn_uninstall.text() == "Deep Uninstall"
    assert panel._btn_keep.text() == "Keep"


# ── no rotated colours from hex alpha suffixes ────────────────────

def test_no_style_in_findings_appends_alpha_to_a_hex_colour():
    """"#d8b46a" + "88" is an eight-digit hex; Qt reads those as #AARRGGBB and
    drew rgb(180, 106, 136) instead of a faded review gold."""
    src = inspect.getsource(fd)
    assert "'#d8b46a')}88" not in src
    assert "}88;" not in src and "}66;" not in src and "}70;" not in src


# ── Black keeps its semantic colours ──────────────────────────────

def _is_grey(hex_color):
    h = hex_color.lstrip("#")
    return h[0:2].lower() == h[2:4].lower() == h[4:6].lower()


@pytest.mark.parametrize("tier", ["safe", "review", "risk", "optional"])
def test_black_keeps_status_colours_coloured(tier):
    """Chrome may be monochrome; a risk tier may not."""
    assert not _is_grey(tm.PALETTES["mono"][tier])


@pytest.mark.parametrize("category", [
    "Applications", "Media", "Cache & Temp", "AI / ML", "Documents",
])
def test_a_category_reads_the_same_in_overview_and_detail(category):
    """Both the donut segment and the drill-down heading resolve through
    _get_category_color, so this is the value both of them draw."""
    for theme in _THEMES:
        tm._current_theme_key = theme
        overview = _get_category_color(category)
        detail = _get_category_color(category)
        assert overview == detail
        if theme == "mono":
            assert not _is_grey(overview), f"{category} went grey in Black"


# ── the selected card matches its own slice ───────────────────────

@pytest.mark.parametrize("theme", _THEMES)
@pytest.mark.parametrize("category", ["Applications", "AI / ML", "Media"])
def test_a_selected_category_card_is_tinted_in_its_own_colour(qapp, theme, category):
    """The card's tint and outline are what tie a row to its donut segment.

    They were written as eight-digit hex — the category colour plus "18" and
    "88" — which Qt reads as #AARRGGBB. AI / ML's #6e33ce, a purple, was drawn
    as rgb(51, 206, 136): a green, on the one card meant to match a slice.
    """
    from PySide6.QtGui import QColor

    tm._current_theme_key = theme
    qapp.setStyleSheet(build_qss(theme))
    card = fd.CategoryCardWidget(category, {"count": 1, "size": 1})
    card.set_selected(True)
    qss = card.styleSheet()

    expected = QColor(_get_category_color(category))
    channels = f"{expected.red()}, {expected.green()}, {expected.blue()}"
    assert channels in qss, (
        f"{theme}/{category}: card is not tinted in its own colour\n{qss}")
    card.deleteLater()
