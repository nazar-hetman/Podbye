"""Category-row hover on the Analyze screen.

These tests render the table and read pixels. That is deliberate: the previous
version of this file asserted QTableWidgetItem.background(), which passed while
the highlight was invisible on screen. The theme styles QTableWidget::item, and
once any QSS rule targets ::item Qt's stylesheet style paints the item panel
itself and ignores the model's brush entirely — so the model was tinted and
nothing was ever drawn. Only a rendered pixel proves the user can see it.
"""
import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QImage, QMouseEvent
from PySide6.QtWidgets import QTableWidgetItem

from app.config.settings_store import SettingsStore
from app.models.finding import Finding
from app.state.scan_state import ScanState
from app.themes.theme_manager import build_qss, get_palette


@pytest.fixture
def screen(qapp):
    # NB this stylesheet stays applied for the rest of the session. Restoring
    # it here re-polishes every widget alive in other test files and faults, so
    # tests that measure sizes must not assume a bare application.
    qapp.setStyleSheet(build_qss("forest"))
    from app.screens.analyze import AnalyzeScreen
    st = ScanState()
    st.set_settings_store(SettingsStore())
    st.set_scan_mode("all")
    s = AnalyzeScreen()
    s.set_scan_state(st)
    s.resize(1200, 800)
    s.show()
    t = s._pf_table
    t.setRowCount(4)
    for r in range(4):
        for c in range(t.columnCount()):
            t.setItem(r, c, QTableWidgetItem(f"cat{r}"))
    qapp.processEvents()
    yield s, st, qapp
    # Each test builds a whole AnalyzeScreen; leaking them exhausts Qt's
    # window/GDI resources part-way through the file and the process dies with
    # an access violation rather than a test failure.
    s.close()
    s.deleteLater()
    qapp.processEvents()


_UNPAINTED = "#000000"   # the fill colour of the blank render target


def _painted(screen_, row, col=0):
    """Colour actually drawn behind a cell."""
    table = screen_._pf_table
    img = QImage(table.viewport().size(), QImage.Format_ARGB32)
    img.fill(Qt.black)
    table.viewport().render(img)
    rect = table.visualItemRect(table.item(row, col))
    return img.pixelColor(rect.center()).name().lower()


def _hover_colour():
    return get_palette().get("panel_hover").lower()


def _selected_colour():
    return get_palette().get("accent_soft").lower()


def _move_to(app, screen_, row):
    table = screen_._pf_table
    rect = table.visualItemRect(table.item(row, 0))
    ev = QMouseEvent(QEvent.MouseMove, QPointF(rect.center()),
                     Qt.NoButton, Qt.NoButton, Qt.NoModifier)
    app.sendEvent(table.viewport(), ev)
    app.processEvents()


def test_hovering_a_row_highlights_it(screen):
    s, _, app = screen
    _move_to(app, s, 2)
    assert _painted(s, 2) == _hover_colour()


def test_the_whole_row_is_highlighted_not_just_the_cell(screen):
    """A per-cell highlight is what Qt gives for free, and it reads as a bug."""
    s, _, app = screen
    _move_to(app, s, 2)
    last_col = s._pf_table.columnCount() - 1
    assert _painted(s, 2, last_col) == _hover_colour()


def test_moving_away_clears_the_previous_row(screen):
    s, _, app = screen
    _move_to(app, s, 2)
    _move_to(app, s, 0)
    assert _painted(s, 0) == _hover_colour()
    assert _painted(s, 2) == _UNPAINTED


def test_leaving_the_table_clears_the_hover(screen):
    s, _, app = screen
    _move_to(app, s, 1)
    app.sendEvent(s._pf_table.viewport(), QEvent(QEvent.Leave))
    app.processEvents()
    assert _painted(s, 1) == _UNPAINTED


def test_hover_works_while_another_row_is_selected(screen):
    s, _, app = screen
    s._on_category_row_clicked(1, 0)
    app.processEvents()
    _move_to(app, s, 3)
    assert _painted(s, 1) == _selected_colour(), "selection lost"
    assert _painted(s, 3) == _hover_colour(), "hover lost while selected"


def test_selection_survives_leaving_the_table(screen):
    s, _, app = screen
    s._on_category_row_clicked(1, 0)
    _move_to(app, s, 3)
    app.sendEvent(s._pf_table.viewport(), QEvent(QEvent.Leave))
    app.processEvents()
    assert _painted(s, 1) == _selected_colour()


def test_hover_survives_a_mid_scan_table_refresh(screen):
    """The table repopulates constantly while scanning; a naive rebuild drops
    the highlight out from under the cursor."""
    s, st, app = screen
    st.add_findings([
        Finding(path=f"C:/x/f{i}.log", name=f"f{i}.log", is_dir=False,
                size_bytes=100, extension=".log", modified=1, accessed=1,
                parent="C:/x") for i in range(20)])
    s._update_partial_table()
    app.processEvents()
    s._hover_row = 0
    s._refresh_partial_table_row_styles()
    app.processEvents()
    assert _painted(s, 0) == _hover_colour()

    st.add_findings([
        Finding(path=f"C:/y/g{i}.tmp", name=f"g{i}.tmp", is_dir=False,
                size_bytes=50, extension=".tmp", modified=1, accessed=1,
                parent="C:/y") for i in range(10)])
    s._update_partial_table()
    app.processEvents()
    assert _painted(s, 0) == _hover_colour(), "refresh wiped the hover"


def test_hover_highlight_is_distinguishable_from_the_panel():
    """The original complaint was an imperceptible highlight."""
    p = get_palette()
    assert p.get("panel_hover", "").lower() != p.get("panel_alt", "").lower()
    assert p.get("panel_hover", "").lower() != p.get("panel", "").lower()
