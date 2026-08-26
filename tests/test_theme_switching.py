"""Controls must follow a theme switch, not keep the theme they were born in.

Switching theme re-applies the stylesheet; it does not rebuild the widget
tree. Anything that read get_palette() once at construction keeps the old
colours for the rest of the session, and the result is a forest-green button
sitting on the paper theme. Reported from the running app: the Ask AI button,
the sort dropdown, and the donut's centre.

These are invisible to every other test in the suite, because nothing else
switches theme after building a screen.
"""
import pytest
from PySide6.QtGui import QColor

from app.themes.theme_manager import THEME_KEYS, build_qss, get_palette


@pytest.fixture
def app(qapp):
    from app.fonts import load_fonts
    load_fonts()
    yield qapp
    qapp.setStyleSheet(build_qss("forest"))


def _dashboard(app):
    from app.config.settings_store import SettingsStore
    from app.models.finding import _format_size
    from app.models.smart_entity import SmartEntity
    from app.screens.findings_dashboard import FindingsDashboard
    from app.state.scan_state import ScanState

    st = ScanState()
    st.set_settings_store(SettingsStore())
    st.set_scan_mode("smart")
    ents = []
    for name, etype, risk in [("App One", "application", "Review"),
                              ("Cache", "cache_folder", "Safe")]:
        e = SmartEntity(path=f"C:/x/{name}", name=name, entity_type=etype)
        e.size_bytes, e.file_count, e.risk = 10 ** 9, 100, risk
        e.size = _format_size(e.size_bytes)
        ents.append(e)
    st._entities = ents
    st._entity_dict_dirty = True

    dash = FindingsDashboard()
    dash.set_scan_state(st)
    dash.resize(1400, 800)
    dash.show()
    st.entities_ready.emit()
    for _ in range(15):
        app.processEvents()
    dash._show_category("Applications")
    for _ in range(15):
        app.processEvents()
    return dash


def _switch(app, theme):
    """Exactly what PodbyeWindow._apply_theme does — stylesheet, no rebuild."""
    app.setStyleSheet(build_qss(theme))
    for _ in range(15):
        app.processEvents()


@pytest.mark.parametrize("theme", THEME_KEYS)
def test_the_sort_dropdown_follows_the_theme(app, theme):
    dash = _dashboard(app)
    try:
        _switch(app, theme)
        sheet = dash._category_view._sort_combo.styleSheet()
        assert get_palette(theme)["panel_alt"] in sheet, (
            f"sort dropdown kept an older theme's colours on {theme}")
    finally:
        dash.close()
        dash.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("theme", THEME_KEYS)
def test_the_ask_ai_button_follows_the_theme(app, theme):
    dash = _dashboard(app)
    try:
        dash._category_view._select_source_row(0)
        for _ in range(10):
            app.processEvents()
        _switch(app, theme)
        panel = dash._category_view._detail_widget
        btn = getattr(panel, "_ai_ask_btn", None)
        if btn is None:
            pytest.skip("inspector not showing an Ask AI button")
        assert get_palette(theme)["accent"] in btn.styleSheet(), (
            f"Ask AI button kept an older theme's accent on {theme}")
    finally:
        dash.close()
        dash.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("theme", THEME_KEYS)
def test_the_donut_hole_matches_the_panel_it_sits_in(app, theme):
    """bg_deep is far darker than the panel on every dark theme, so the hole
    read as a black puck there while blending correctly on the light one.
    One rule for all four: the hole is the panel colour."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage
    from app.screens.findings_dashboard import DonutChartWidget

    _switch(app, theme)
    donut = DonutChartWidget()
    donut.setFixedSize(240, 240)
    donut.set_data([("Applications", {"size_bytes": 10 ** 10, "count": 3,
                                      "percentage": 60.0}),
                    ("Cache & Temp", {"size_bytes": 6 * 10 ** 9, "count": 4,
                                      "percentage": 40.0})], 16 * 10 ** 9)
    donut.show()
    for _ in range(10):
        app.processEvents()

    img = QImage(donut.size(), QImage.Format_ARGB32)
    img.fill(Qt.magenta)
    donut.render(img)
    centre = img.pixelColor(donut.width() // 2, donut.height() // 2 + 40)
    expected = QColor(get_palette(theme)["panel_alt"])
    assert centre == expected, (
        f"{theme}: donut hole is {centre.name()}, panel is {expected.name()}")

    donut.close()
    donut.deleteLater()
    app.processEvents()
