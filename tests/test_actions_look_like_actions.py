"""A container must not flatten the controls inside it.

Reported on Startups: "Open in Explorer and Copy path do not visually read as
buttons/actions". They were `#Subtle`, which the application stylesheet gives a
border and a fill — and they had neither.

The cause is a Qt rule that is easy to trip: a widget stylesheet with **no
selector** cascades to every descendant *and* outranks the application
stylesheet. So a panel saying

    self.setStyleSheet("background: transparent; border: none;")

quietly removes the border from every button, checkbox and combo box it holds.
Measured on that panel: the same button painted #3a4f42 at its corner standing
alone and #000000 — nothing at all — inside it.

Twenty container sites across seven screens were doing this. They now go
through ``style_container()``, which scopes the rules to the container's own
class and object name.

These tests measure the symptom rather than grepping for the cause, so a new
container that reintroduces it is caught wherever it appears.
"""
import importlib

import pytest
from PySide6.QtWidgets import QPushButton

from app.themes.theme_manager import build_qss
from app.widgets.controls import style_container


SCREENS = [
    ("app.screens.quick_cleanup", "QuickCleanupScreen"),
    ("app.screens.history", "HistoryScreen"),
    ("app.screens.home", "HomeScreen"),
    ("app.screens.analyze", "AnalyzeScreen"),
    ("app.screens.settings", "SettingsScreen"),
    ("app.screens.startups", "StartupsScreen"),
]


def _edges(btn):
    """The colours a button paints around its own rim."""
    img = btn.grab().toImage()
    w, h = img.width(), img.height()
    pts = [(1, 1), (w - 2, 1), (1, h - 2), (w - 2, h - 2),
           (w // 2, 0), (0, h // 2)]
    return {img.pixelColor(x, y).name() for x, y in pts}


def _paints_nothing(btn) -> bool:
    """A grab is black where the widget painted no pixels at all."""
    return btn.width() > 6 and btn.height() > 6 and _edges(btn) == {"#000000"}


def _twin(btn, qapp):
    """The same button, outside any container."""
    twin = QPushButton(btn.text())
    twin.setObjectName(btn.objectName())
    if btn.styleSheet():
        twin.setStyleSheet(btn.styleSheet())
    twin.resize(max(btn.width(), 90), max(btn.height(), 24))
    twin.show()
    qapp.processEvents()
    return twin


def _stripped(root, qapp):
    """Buttons that paint chrome alone and none where they actually live."""
    out = []
    for btn in root.findChildren(QPushButton):
        if not _paints_nothing(btn):
            continue
        twin = _twin(btn, qapp)
        try:
            if _edges(twin) - {"#000000"}:
                out.append(btn.text() or btn.objectName() or "(unnamed)")
        finally:
            twin.deleteLater()
    return out


def _built(qapp, modname, cls):
    qapp.setStyleSheet(build_qss("forest"))
    screen = getattr(importlib.import_module(modname), cls)()
    screen.resize(1400, 900)
    screen.show()
    for _ in range(4):
        qapp.processEvents()
    return screen


def _populated_startups(qapp):
    """The screen as a person sees it: results listed, an entry selected.

    A fresh screen shows its intro; the inspector — and therefore the two
    buttons this pass is about — is not built until results exist. Auditing
    the empty state is what let the second copy of the bug through: the
    constructor's stylesheet was scoped, and apply_style() re-set it
    unscoped, on a widget no audit had reached.
    """
    import time
    import app.screens.startups as st
    from app.models.startup_entry import StartupEntry

    qapp.setStyleSheet(build_qss("forest"))
    entry = StartupEntry(
        name="Grammarly", command="C:/g.exe", path="C:/g.exe",
        publisher="Grammarly Inc.", source="run_hkcu",
        source_label="User startup registry", enabled=True, risk="Optional",
        risk_reason="r", impact="Creative helper")
    entry.target_modified = time.time() - 30 * 86400
    screen = st.StartupsScreen()
    screen.resize(1900, 1000)
    screen.show()
    screen._entries = [entry]
    screen._filtered = [entry]
    screen._show_results()
    for _ in range(6):
        qapp.processEvents()
    screen._select_entry(entry.key)
    for _ in range(6):
        qapp.processEvents()
    return screen


# -- the reported case ---------------------------------------------

def _visibility_chain(widget, root, screen, panel):
    """Why isVisibleTo() said no, for a failure that only happens on CI.

    isVisibleTo(root) is false when any widget between `widget` and `root` is
    explicitly hidden, so the answer is always a single named ancestor. This
    walks the chain and reports it, plus the selection state that decides
    whether the action frame was ever shown. Diagnostic only — it runs after
    the assertion has already failed and changes no timing.
    """
    out = ["", "visibility chain from the button up to the screen:"]
    first_hidden = None
    w = widget
    while w is not None:
        out.append("  %-22s objectName=%-14r isVisible=%-5s isHidden=%-5s "
                   "isEnabled=%-5s%s"
                   % (type(w).__name__, w.objectName(), w.isVisible(),
                      w.isHidden(), w.isEnabled(),
                      "   <-- screen" if w is root else ""))
        if first_hidden is None and w.isHidden():
            first_hidden = w
        if w is root:
            break
        w = w.parentWidget()
    if w is None:
        out.append("  !! reached a top-level widget without passing the screen")

    out.append("")
    out.append("first hidden ancestor: %s" % (
        "none — nothing in the chain reports isHidden()" if first_hidden is None
        else "%s objectName=%r" % (type(first_hidden).__name__,
                                   first_hidden.objectName())))

    # If set_entry never ran with a real entry the action frame stays hidden
    # from construction, which looks identical from the button's side.
    entry = getattr(panel, "_current_entry", "<missing>")
    out.append("panel._current_entry: %r" % (
        getattr(entry, "key", entry) if entry not in (None, "<missing>") else entry))
    out.append("screen._selected_key: %r" % getattr(screen, "_selected_key", "<missing>"))
    out.append("len(screen._filtered): %r" % len(getattr(screen, "_filtered", []) or []))
    out.append("len(screen._entries): %r" % len(getattr(screen, "_entries", []) or []))
    out.append("screen.isVisible(): %r" % screen.isVisible())
    return "\n".join(out)


def test_the_startup_inspector_actions_read_as_buttons(qapp):
    """Reported twice: once against the panel's own stylesheet, and again
    against the copy its sidebar re-applies on every theme change."""
    screen = _populated_startups(qapp)
    try:
        panel = screen._right_sidebar.detail_widget
        for btn in (panel._open_btn, panel._copy_btn):
            assert btn.isVisibleTo(screen), _visibility_chain(
                btn, screen, screen, panel)
            assert _edges(btn) - {"#000000"}, f"{btn.text()} paints no chrome"
    finally:
        screen.deleteLater()
        qapp.processEvents()


def test_the_populated_startups_screen_flattens_nothing(qapp):
    screen = _populated_startups(qapp)
    try:
        assert _stripped(screen, qapp) == []
    finally:
        screen.deleteLater()
        qapp.processEvents()


def test_a_theme_switch_does_not_strip_them_again(qapp):
    """apply_style() runs on every switch, and that is where the second copy
    of the rules lived."""
    screen = _populated_startups(qapp)
    try:
        qapp.setStyleSheet(build_qss("amber"))
        screen._right_sidebar.apply_style()
        for _ in range(4):
            qapp.processEvents()

        panel = screen._right_sidebar.detail_widget
        for btn in (panel._open_btn, panel._copy_btn):
            assert _edges(btn) - {"#000000"}, f"{btn.text()} lost its chrome"
    finally:
        qapp.setStyleSheet(build_qss("forest"))
        screen.deleteLater()
        qapp.processEvents()


# -- and nowhere else does it -------------------------------------

@pytest.mark.parametrize("modname,cls", SCREENS)
def test_no_screen_flattens_its_own_controls(qapp, modname, cls):
    screen = _built(qapp, modname, cls)
    try:
        assert _stripped(screen, qapp) == []
    finally:
        screen.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize("modname,cls", SCREENS)
def test_no_container_carries_a_selectorless_stylesheet(qapp, modname, cls):
    """The cause, checked directly: rules with no selector reach every child.

    A widget with no children cannot hurt anyone, so labels styling themselves
    are fine — this is only about containers.
    """
    from PySide6.QtWidgets import QAbstractButton, QComboBox, QLineEdit

    screen = _built(qapp, modname, cls)
    try:
        offenders = []
        for w in [screen] + screen.findChildren(object):
            qss = getattr(w, "styleSheet", lambda: "")()
            if not qss or "{" in qss:
                continue
            if not any(k in qss for k in ("background", "border")):
                continue
            controls = (w.findChildren(QAbstractButton) + w.findChildren(QComboBox)
                        + w.findChildren(QLineEdit))
            if controls:
                offenders.append(
                    f"{w.metaObject().className()}#{w.objectName() or '-'}: {qss[:40]}")
        assert offenders == [], (
            "use style_container() so the rules stay on the container:\n  "
            + "\n  ".join(offenders))
    finally:
        screen.deleteLater()
        qapp.processEvents()


# -- the helper keeps its promise ----------------------------------

def test_style_container_scopes_what_it_is_given(qapp):
    from PySide6.QtWidgets import QFrame, QVBoxLayout

    frame = QFrame()
    QVBoxLayout(frame).addWidget(QPushButton("act"))
    style_container(frame, "background: transparent; border: none;")

    assert "{" in frame.styleSheet()
    assert frame.objectName(), "a selector needs something to bind to"
    assert frame.metaObject().className() in frame.styleSheet()


def test_style_container_keeps_an_existing_object_name(qapp):
    from PySide6.QtWidgets import QFrame

    frame = QFrame()
    frame.setObjectName("PanelAlt")
    style_container(frame, "border: none;")

    assert frame.objectName() == "PanelAlt", "the name is what the theme styles"
    assert "QFrame#PanelAlt" in frame.styleSheet()


# -- and the list stays responsive while it is being updated -------
#
# Reported as "interacting with filters can noticeably freeze the UI while
# startup analysis is running". Measured: a filter keystroke cost 73 ms with 25
# entries, 306 ms with 100 and 1034 ms with 300 — and cost the same whether or
# not an AI pass was running (73.7 ms idle against 74.3 ms during). The
# analysis was never the cause.
#
# Profiled: 451 setStyleSheet calls per keystroke, 92% of the time. Each row
# was restyled three times over — once on rebind, once when the loop set its
# selection, once more by a selection sync afterwards — and each pass set six
# sheets. Restyling only on a change brings the same keystroke to 10 ms.


def _startup_screen(qapp, count):
    import time as _time
    import app.screens.startups as st
    from app.models.startup_entry import StartupEntry

    qapp.setStyleSheet(build_qss("forest"))
    screen = st.StartupsScreen()
    rows = []
    for i in range(count):
        entry = StartupEntry(
            name=f"Startup Entry {i:03d}", command=f"C:/A{i}/a.exe",
            path=f"C:/A{i}/a.exe", publisher=f"Vendor {i}", source="run_hkcu",
            source_label="User startup registry", enabled=bool(i % 3),
            risk=("Optional", "Review", "Safe")[i % 3], risk_reason="r",
            impact="Background sync")
        entry.target_modified = _time.time() - 40 * 86400
        rows.append(entry)
    screen._entries = rows
    screen._filtered = list(rows)
    screen.resize(1500, 900)
    screen.show()
    screen._show_results()
    for _ in range(6):
        qapp.processEvents()
    return screen


def test_a_row_is_not_restyled_when_nothing_about_it_changed(qapp):
    """setStyleSheet reparses and repolishes; it is the expensive call here."""
    screen = _startup_screen(qapp, 6)
    try:
        row = next(iter(screen._row_widgets.values()))
        calls = []
        original = row.setStyleSheet
        row.setStyleSheet = lambda qss: (calls.append(qss), original(qss))[1]

        row.update_entry(row._entry)
        row.set_selected(False)

        assert calls == [], "restyled without a change"
    finally:
        screen.deleteLater()
        qapp.processEvents()


def test_a_real_change_still_restyles(qapp):
    """The guard must not make the row stale."""
    screen = _startup_screen(qapp, 6)
    try:
        row = next(iter(screen._row_widgets.values()))
        row.set_selected(False)
        before = row.styleSheet()

        row.set_selected(True)

        assert row.styleSheet() != before
    finally:
        screen.deleteLater()
        qapp.processEvents()


def test_filtering_does_not_rebuild_the_rows(qapp):
    """Rebinding a pooled row instead of constructing one, as Findings does."""
    screen = _startup_screen(qapp, 12)
    try:
        first = list(screen._row_widgets.values())[0]
        screen._search = "Entry 0"
        screen._reapply_filters()
        screen._search = ""
        screen._reapply_filters()

        assert list(screen._row_widgets.values())[0] is first
    finally:
        screen.deleteLater()
        qapp.processEvents()


def test_typing_does_not_filter_on_every_character(qapp):
    """The same debounce the Findings search uses."""
    screen = _startup_screen(qapp, 6)
    try:
        screen._on_search_typed("E")

        assert screen._search_timer.isActive(), "filtered synchronously"
    finally:
        screen.deleteLater()
        qapp.processEvents()


def test_clearing_the_box_is_immediate(qapp):
    """Asking to see everything again must not wait on a timer."""
    screen = _startup_screen(qapp, 6)
    try:
        screen._on_search_typed("Entry 000")
        screen._search_timer.stop()
        screen._on_search_typed("")

        assert not screen._search_timer.isActive()
        assert len(screen._filtered) == 6
    finally:
        screen.deleteLater()
        qapp.processEvents()
