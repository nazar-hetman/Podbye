"""Text must fit, wrap, or elide — never simply stop.

Every screen is fed hostile content (a 68-character scheduled-task name, a
deep path, three sentences of generated prose) in more than one language and at
more than one window width, then every label is measured against what it would
need.

Two rules learned while writing this, both of which produced false alarms:

* **Load the fonts.** A bare test process has different metrics. Measured
  without them, the PROTECTED badge and the Disable button both looked clipped
  by a few pixels; with the app's own fonts, as main() loads them, both fit.
* **Ask the widget, not the font.** Judging a button by its text width plus an
  assumed padding flagged a link-style button that sets `padding: 0` and fits
  exactly. `sizeHint()` already knows what the style adds.

There are three ways to not fit, so there are three checks: cut sideways, cut
below, and elided down to nothing but the ellipsis.
"""
import importlib
import time

import pytest
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel, QPushButton

from app.fonts import FONT_UI, load_fonts
from app.i18n import get_language, set_language
from app.themes.theme_manager import build_qss
from app.widgets.controls import ElidedLabel


LONG_NAME = "OneDrive Startup Task-S-1-5-21-1111111111-222222222-3333333333-1001"
LONG_PATH = ("C:/Users/ExampleUser/AppData/Local/Programs/SomeVendor/Application"
             "/resources/app.asar.unpacked/node_modules/@scope/package/dist")
LONG_PROSE = ("This program registers a helper that starts with the session and "
              "keeps background features available across other applications. ") * 3

SCREENS = [
    ("app.screens.quick_cleanup", "QuickCleanupScreen"),
    ("app.screens.history", "HistoryScreen"),
    ("app.screens.home", "HomeScreen"),
    ("app.screens.settings", "SettingsScreen"),
    ("app.screens.analyze", "AnalyzeScreen"),
]


@pytest.fixture
def dressed(qapp):
    """The application as main() dresses it: fonts loaded, theme applied."""
    previous_font, previous_lang = qapp.font(), get_language()
    load_fonts()
    qapp.setFont(QFont(FONT_UI, 10))
    qapp.setStyleSheet(build_qss("forest"))
    yield qapp
    qapp.setFont(previous_font)
    set_language(previous_lang)


def _faults(widget):
    out = []
    for lbl in widget.findChildren(QLabel):
        if not lbl.isVisibleTo(widget) or lbl.width() < 4 or not lbl.text():
            continue
        if isinstance(lbl, ElidedLabel):
            if not lbl.text().strip().strip("\u2026.") and len(lbl.full_text()) > 3:
                out.append(f"elided away: {lbl.full_text()[:40]!r}")
            continue
        if lbl.wordWrap():
            want = lbl.heightForWidth(lbl.width())
            if want > lbl.height() + 1:
                out.append(f"cut below ({lbl.height()} of {want}px): "
                           f"{lbl.text()[:40]!r}")
            continue
        if lbl.sizeHint().width() > lbl.width() + 1:
            out.append(f"cut sideways ({lbl.width()} of "
                       f"{lbl.sizeHint().width()}px): {lbl.text()[:40]!r}")
    for btn in widget.findChildren(QPushButton):
        if not btn.isVisibleTo(widget) or not btn.text():
            continue
        if btn.sizeHint().width() > btn.width() + 1:
            out.append(f"button cut ({btn.width()} of "
                       f"{btn.sizeHint().width()}px): {btn.text()[:40]!r}")
    return out


def _settled(qapp, widget, width):
    widget.resize(width, 900)
    widget.show()
    for _ in range(6):
        qapp.processEvents()
    return widget


def _startups(qapp, monkeypatch, hostile=True):
    import app.screens.startups as st
    from app.models.startup_entry import StartupEntry

    screen = st.StartupsScreen()
    rows = []
    for i in range(6):
        entry = StartupEntry(
            name=(LONG_NAME if hostile else f"Entry {i}"), command=LONG_PATH,
            path=LONG_PATH,
            publisher="A Very Long Publisher Name Corporation International",
            source="run_hkcu",
            source_label="Scheduled task (logon, per-user, elevated)",
            enabled=bool(i % 2), risk=("Optional", "Review", "Protected")[i % 3],
            risk_reason=LONG_PROSE, impact="Remote access service")
        entry.target_modified = time.time() - 5 * 365 * 86400
        entry.ai_status = ("ready", "analyzing", "failed")[i % 3]
        entry.ai_explanation = LONG_PROSE
        entry.explanation_fallback = LONG_PROSE
        rows.append(entry)
    screen._entries = rows
    screen._filtered = list(rows)
    # showEvent() re-reads the machine, so without this the *real* startup
    # list replaces these rows the moment the screen is shown and the
    # selection is cleared — the panel then renders its empty state and every
    # assertion below silently audits nothing. It only showed up
    # intermittently because _last_refresh is a class attribute with a 3s
    # throttle, so whether it fired depended on what ran before.
    # monkeypatch, not a bare assignment: a plain assignment here is never
    # undone, so the stub replaced the real detector for every test that
    # ran after this file in the same session.
    import app.services.startup_detector as detector
    monkeypatch.setattr(detector, "detect_startup_entries",
                        lambda: list(rows))

    screen._show_results()
    for _ in range(6):
        qapp.processEvents()
    screen._select_entry(rows[0].key)
    for _ in range(6):
        qapp.processEvents()
    return screen


def _findings(qapp):
    import app.screens.findings_dashboard as fd

    ents = [{
        "path": LONG_PATH, "name": LONG_NAME, "entity_type": "application",
        "size_bytes": 10 ** 10 + i, "size": "9.31 GB", "file_count": 123456,
        "folder_count": 7890, "risk": ("Safe", "Review", "Protected")[i % 3],
        "category": "Applications",
        "entity_type_label": "Installed application",
        "actionability": "recycle", "children_sample": [],
        "ai_status": "ready", "ai_explanation": LONG_PROSE} for i in range(6)]
    view = fd.CategoryDetailView()
    view._app_index_cache = {}
    view.set_category("Applications", ents)
    for _ in range(6):
        qapp.processEvents()
    return view


# -- the screen the pass started from ------------------------------

@pytest.mark.parametrize("width", [1600, 1100])
def test_startups_fits_hostile_content(dressed, width, monkeypatch):
    screen = _settled(dressed, _startups(dressed, monkeypatch), width)
    try:
        assert _faults(screen) == []
    finally:
        screen.deleteLater()
        dressed.processEvents()


def test_startups_fits_a_longer_language(dressed, monkeypatch):
    """Ukrainian is the reference locale and its strings run longer."""
    set_language("Ukrainian")
    screen = _settled(dressed, _startups(dressed, monkeypatch), 1100)
    try:
        assert _faults(screen) == []
        assert "Заплановане завдання" in screen._detail_widget._meta_lbl.text()
        assert screen._detail_widget._impact_lbl.text() == (
            "Служба віддаленого доступу")
        assert screen._detail_widget._risk_badge.text() == "НЕОБОВ’ЯЗКОВО ПІД ЧАС ЗАПУСКУ"
    finally:
        screen.deleteLater()
        dressed.processEvents()


@pytest.mark.parametrize("width", [1600, 1100])
def test_startups_fits_polish_at_supported_widths(dressed, width, monkeypatch):
    set_language("Polish")
    screen = _settled(dressed, _startups(dressed, monkeypatch), width)
    try:
        assert _faults(screen) == []
        assert "Scheduled task" not in screen._detail_widget._meta_lbl.text()
    finally:
        screen.deleteLater()
        dressed.processEvents()


@pytest.mark.parametrize("width", [1600, 1100])
def test_startups_fits_german_at_supported_widths(dressed, width, monkeypatch):
    set_language("German")
    screen = _settled(dressed, _startups(dressed, monkeypatch), width)
    try:
        assert _faults(screen) == []
        assert "Scheduled task" not in screen._detail_widget._meta_lbl.text()
    finally:
        screen.deleteLater()
        dressed.processEvents()


@pytest.mark.parametrize("width", [1600, 1100])
def test_startups_fits_spanish_at_supported_widths(dressed, width, monkeypatch):
    """Spanish recommendations and metadata must survive hostile raw values."""
    set_language("Spanish")
    screen = _settled(dressed, _startups(dressed, monkeypatch), width)
    try:
        assert _faults(screen) == []
        assert "Scheduled task" not in screen._detail_widget._meta_lbl.text()
    finally:
        screen.deleteLater()
        dressed.processEvents()


# -- and the rest of the application -------------------------------

@pytest.mark.parametrize("width", [1600, 1100])
def test_findings_fits_hostile_content(dressed, width, monkeypatch):
    view = _settled(dressed, _findings(dressed), width)
    try:
        assert _faults(view) == []
    finally:
        view.deleteLater()
        dressed.processEvents()


def test_findings_fits_hostile_content_in_ukrainian_at_minimum_width(dressed):
    """The inspector's Keep action and status are longer in Ukrainian."""
    set_language("Ukrainian")
    view = _settled(dressed, _findings(dressed), 1100)
    try:
        view._select_source_row(0)
        for _ in range(6):
            dressed.processEvents()
        assert _faults(view) == []
        assert "entities" not in view._stats_lbl.text()
        assert "reviewed" not in view._ai_summary_lbl.text()
        assert view._right_sidebar.detail_widget._btn_keep.text() == (
            "Виключити з очищення")
    finally:
        view.deleteLater()
        dressed.processEvents()


@pytest.mark.parametrize("width", [1600, 1100])
def test_findings_fits_polish_at_supported_widths(dressed, width):
    set_language("Polish")
    view = _settled(dressed, _findings(dressed), width)
    try:
        view._select_source_row(0)
        for _ in range(6):
            dressed.processEvents()
        assert _faults(view) == []
        assert "entities" not in view._stats_lbl.text()
        assert "reviewed" not in view._ai_summary_lbl.text()
    finally:
        view.deleteLater()
        dressed.processEvents()


@pytest.mark.parametrize("width", [1600, 1100])
def test_findings_fits_german_at_supported_widths(dressed, width):
    set_language("German")
    view = _settled(dressed, _findings(dressed), width)
    try:
        view._select_source_row(0)
        for _ in range(6):
            dressed.processEvents()
        assert _faults(view) == []
        assert "entities" not in view._stats_lbl.text()
        assert "reviewed" not in view._ai_summary_lbl.text()
    finally:
        view.deleteLater()
        dressed.processEvents()


@pytest.mark.parametrize("width", [1600, 1100])
def test_findings_fits_spanish_at_supported_widths(dressed, width):
    """Long Spanish inspector text wraps rather than clipping at either width."""
    set_language("Spanish")
    view = _settled(dressed, _findings(dressed), width)
    try:
        view._select_source_row(0)
        for _ in range(6):
            dressed.processEvents()
        assert _faults(view) == []
        assert "entities" not in view._stats_lbl.text()
        assert "reviewed" not in view._ai_summary_lbl.text()
    finally:
        view.deleteLater()
        dressed.processEvents()


@pytest.mark.parametrize("modname,cls", SCREENS)
def test_every_screen_fits_its_own_text(dressed, modname, cls):
    screen = _settled(dressed,
                      getattr(importlib.import_module(modname), cls)(), 1100)
    try:
        assert _faults(screen) == []
    finally:
        screen.deleteLater()
        dressed.processEvents()


@pytest.mark.parametrize("modname,cls", SCREENS)
def test_every_screen_fits_a_longer_language(dressed, modname, cls):
    set_language("Ukrainian")
    screen = _settled(dressed,
                      getattr(importlib.import_module(modname), cls)(), 1100)
    try:
        assert _faults(screen) == []
    finally:
        screen.deleteLater()
        dressed.processEvents()


@pytest.mark.parametrize("modname,cls", SCREENS)
def test_every_screen_fits_polish_at_minimum_width(dressed, modname, cls):
    set_language("Polish")
    screen = _settled(dressed,
                      getattr(importlib.import_module(modname), cls)(), 1100)
    try:
        assert _faults(screen) == []
    finally:
        screen.deleteLater()
        dressed.processEvents()


@pytest.mark.parametrize("modname,cls", SCREENS)
def test_every_screen_fits_german_at_minimum_width(dressed, modname, cls):
    set_language("German")
    screen = _settled(dressed,
                      getattr(importlib.import_module(modname), cls)(), 1100)
    try:
        assert _faults(screen) == []
    finally:
        screen.deleteLater()
        dressed.processEvents()


@pytest.mark.parametrize("modname,cls", SCREENS)
def test_every_screen_fits_spanish_at_minimum_width(dressed, modname, cls):
    set_language("Spanish")
    screen = _settled(dressed,
                      getattr(importlib.import_module(modname), cls)(), 1100)
    try:
        assert _faults(screen) == []
    finally:
        screen.deleteLater()
        dressed.processEvents()


# -- controls must fit the space the layout gave them ---------------
#
# Reported as "buttons partially visible" on the Settings AI tab. Test and
# Refresh painted 44px tall in a row the layout had reserved 32 for, so their
# bottom 6px were clipped.
#
# The cause is a Qt trap worth naming. setFixedHeight(32) sets the minimum and
# the maximum together; applying the theme then raises the *minimum* to what
# the style needs — #Ghost's 7px of vertical padding plus the font puts it at
# 44 — and leaves the maximum at 32. Qt honours the minimum, the layout
# reserved the maximum, and the difference is clipped. Measured in isolation:
#
#     setFixedHeight(32)        -> min 32, max 32
#     app.setStyleSheet(theme)  -> min 44, max 32
#
# 18 controls across Settings and Analyze were in that state. The fixed heights
# are minimums now, which is what they were always for: the comment beside one
# of them recalls a button that once rendered 12px tall.


def _all_widgets(root):
    from PySide6.QtWidgets import QWidget
    return [root] + root.findChildren(QWidget)


def _impossible_constraints(root):
    out = []
    for w in _all_widgets(root):
        for dim, mn, mx in (("height", w.minimumHeight(), w.maximumHeight()),
                            ("width", w.minimumWidth(), w.maximumWidth())):
            if 0 < mx < mn:
                text = (getattr(w, "text", lambda: "")() or w.objectName()
                        or w.metaObject().className())
                out.append(f"{text[:26]!r} {dim}: min {mn} > max {mx}")
    return out


def _spills(root):
    """Widgets drawn outside the parent that clips them.

    A scroll area's content is meant to exceed its viewport — that is what
    scrolling is — so anything inside one is left alone.
    """
    from PySide6.QtWidgets import QAbstractScrollArea, QWidget

    scrolled = set()
    for area in root.findChildren(QAbstractScrollArea):
        # Only QScrollArea owns a content widget; item views (QListView and
        # friends) are scroll areas too and have no such thing.
        content = getattr(area, "widget", None)
        content = content() if callable(content) else None
        if content is not None:
            scrolled.add(id(content))
    out = []
    for w in root.findChildren(QWidget):
        parent = w.parentWidget()
        if parent is None or id(w) in scrolled:
            continue
        if not w.isVisibleTo(root) or w.height() < 4 or parent.height() < 4:
            continue
        over = (w.y() + w.height()) - parent.height()
        if over > 1:
            text = (getattr(w, "text", lambda: "")() or w.objectName()
                    or w.metaObject().className())
            out.append(f"{text[:26]!r} spills {over}px below its parent")
    return out


def test_the_settings_actions_are_not_clipped(dressed):
    """The reported case: Test and Refresh on the AI tab."""
    import app.screens.settings as se

    screen = _settled(dressed, se.SettingsScreen(), 1919)
    try:
        screen._switch_section("ai")
        for _ in range(6):
            dressed.processEvents()
        for btn in (screen._btn_test, screen._btn_refresh_models):
            parent = btn.parentWidget()
            assert btn.y() + btn.height() <= parent.height() + 1, (
                f"{btn.text()} is clipped by its row")
    finally:
        screen.deleteLater()
        dressed.processEvents()


@pytest.mark.parametrize("section", ["general", "ai", "scan", "about"])
def test_no_settings_section_asks_for_an_impossible_size(dressed, section):
    import app.screens.settings as se

    screen = _settled(dressed, se.SettingsScreen(), 1919)
    try:
        screen._switch_section(section)
        for _ in range(6):
            dressed.processEvents()
        assert _impossible_constraints(screen) == [], (
            "setFixedHeight below the theme's own minimum clips the widget; "
            "use setMinimumHeight")
        assert _spills(screen) == []
    finally:
        screen.deleteLater()
        dressed.processEvents()


@pytest.mark.parametrize("section", ["general", "ai", "scan", "about"])
def test_spanish_settings_sections_have_no_clipped_constraints(dressed, section):
    set_language("Spanish")
    import app.screens.settings as se

    screen = _settled(dressed, se.SettingsScreen(), 1100)
    try:
        screen._switch_section(section)
        for _ in range(6):
            dressed.processEvents()
        assert _impossible_constraints(screen) == []
        assert _spills(screen) == []
        assert _faults(screen) == []
    finally:
        screen.deleteLater()
        dressed.processEvents()


@pytest.mark.parametrize("modname,cls", SCREENS)
def test_no_screen_asks_for_an_impossible_size(dressed, modname, cls):
    screen = _settled(dressed,
                      getattr(importlib.import_module(modname), cls)(), 1600)
    try:
        assert _impossible_constraints(screen) == []
        assert _spills(screen) == []
    finally:
        screen.deleteLater()
        dressed.processEvents()


def test_a_fixed_height_below_the_theme_minimum_is_the_trap(dressed):
    """Pinned as a demonstration: this is why the rule above exists."""
    from PySide6.QtWidgets import QPushButton

    btn = QPushButton("Test")
    btn.setObjectName("Ghost")
    btn.setFixedHeight(32)
    btn.show()
    dressed.processEvents()
    try:
        assert btn.minimumHeight() > btn.maximumHeight(), (
            "the theme no longer raises the minimum — the trap has moved")
    finally:
        btn.deleteLater()
        dressed.processEvents()


# -- and nothing may demand more width than its viewport gives ------
#
# Reported as "long text does not fit" with a scheduled-task name on screen:
# the name, the launch path, the recommendation and the assessment all ran past
# the right edge of the Startups inspector.
#
# Nothing was clipping *itself* — every label had the width it asked for. The
# panel had asked for 576px inside a 533px viewport whose horizontal scrollbar
# is deliberately off, so the surplus simply had nowhere to go. Two widgets
# were demanding it:
#
#   * ElidedLabel did not override minimumSizeHint(), so a label whose whole
#     purpose is to shrink still asked for the full width of its text - 511px
#     for the launch path;
#   * the name was a wrapping QLabel, and a wrapping QLabel reports its longest
#     unbreakable word as its minimum - 405px for that task name. Wrapping had
#     been the previous attempt at this exact problem and could not work.
#
# The Findings inspector had the same fault, from the same two causes.
#
# The app's minimum window is 1100x700 (main.py), so that is the narrowest
# width worth asserting about.


def _viewport_overflows(root):
    """Scroll areas whose content cannot fit and cannot be scrolled to."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QScrollArea

    out = []
    for area in root.findChildren(QScrollArea):
        content = area.widget()
        if content is None or not area.isVisibleTo(root):
            continue
        if area.horizontalScrollBarPolicy() != Qt.ScrollBarAlwaysOff:
            continue                      # it can be scrolled to
        need, have = content.minimumSizeHint().width(), area.viewport().width()
        if have > 4 and need > have + 1:
            out.append(f"{content.metaObject().className()} needs {need}px "
                       f"in {have}px")
    return out


@pytest.mark.parametrize("width", [1919, 1400, 1100])
def test_the_startup_inspector_fits_its_sidebar(dressed, width, monkeypatch):
    screen = _settled(dressed, _startups(dressed, monkeypatch), width)
    try:
        assert _viewport_overflows(screen) == []
    finally:
        screen.deleteLater()
        dressed.processEvents()


@pytest.mark.parametrize("language", [
    "English", "Ukrainian", "Spanish", "German", "French", "Polish",
])
def test_startup_inspector_actions_fit_every_shipped_locale_at_minimum_width(
        dressed, language, monkeypatch):
    """The inspector must not regain a horizontal minimum through its actions.

    Its viewport is only a little wider than 280px at the application's
    1100px minimum window width.  This exercises the real populated state,
    including the longest shipped Task Manager labels, rather than measuring
    an empty panel.
    """
    set_language(language)
    screen = _settled(dressed, _startups(dressed, monkeypatch), 1100)
    try:
        assert _viewport_overflows(screen) == []
        assert _faults(screen) == []
    finally:
        screen.deleteLater()
        dressed.processEvents()


@pytest.mark.parametrize("width", [1919, 1400, 1100])
def test_the_findings_inspector_fits_its_sidebar(dressed, width, monkeypatch):
    view = _settled(dressed, _findings(dressed), width)
    try:
        assert _viewport_overflows(view) == []
    finally:
        view.deleteLater()
        dressed.processEvents()


def test_an_elided_label_asks_for_almost_nothing(dressed):
    """It elides; demanding the width of the full text defeats the point."""
    label = ElidedLabel("C:/a/very/long/path/that/nothing/can/break/up/at/all")
    label.show()
    dressed.processEvents()
    try:
        assert label.minimumSizeHint().width() < 40
        assert label.sizeHint().width() > 200, "the natural width still stands"
    finally:
        label.deleteLater()
        dressed.processEvents()


def test_an_inspector_title_does_not_demand_its_longest_word(dressed, monkeypatch):
    """One unbreakable token must not set the panel's minimum width."""
    import app.screens.startups as st
    import app.screens.findings_dashboard as fd

    startups_panel = st.StartupInspectorPanel(compact=True)
    # Held, not borrowed: the sidebar owns the panel, and letting it fall out
    # of scope collects it mid-test while its labels are still being read.
    findings_sidebar = fd.RightSidebar(open_cb=lambda p: None,
                                       copy_cb=lambda p: None)
    try:
        for panel in (startups_panel, findings_sidebar.detail_widget):
            panel._name_lbl.setText(LONG_NAME)
            dressed.processEvents()
            assert panel._name_lbl.minimumSizeHint().width() < 60, (
                f"{panel.metaObject().className()} title demands its own width")
    finally:
        startups_panel.deleteLater()
        findings_sidebar.deleteLater()
        dressed.processEvents()
