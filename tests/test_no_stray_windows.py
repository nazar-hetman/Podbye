"""Rebuilding a screen must not put extra windows on the desktop.

Screens here throw their content away and build it again — Home on every
navigation, Startups whenever Analyze runs, Quick Cleanup on every rescan.
Clearing a layout has to do two things at once, and they pull against each
other:

  * the outgoing widget must stop painting immediately, because deleteLater()
    only *queues* the delete and until it runs the widget still draws at its
    old geometry on top of whatever replaced it;
  * it must not be unparented to achieve that, because a widget with no parent
    is a top-level window. Clicking Analyze on Startups opened a blank 200x64
    frame over the app — one orphaned row per startup entry, stacked.

hide() satisfies both. This pins that: after every rebuild-heavy action, the
screen itself is the only window on screen.
"""
import pytest
from PySide6.QtWidgets import QApplication, QWidget

from app.config.settings_store import SettingsStore
from app.state.scan_state import ScanState
from app.themes.theme_manager import build_qss


@pytest.fixture(scope="module")
def app(qapp):
    from app.fonts import load_fonts
    load_fonts()
    qapp.setStyleSheet(build_qss("forest"))
    return qapp


def _visible_windows() -> set[int]:
    """Identity of every window currently on screen.

    Compared as a before/after delta, not an absolute count: other test files
    in the same session leave widgets alive, and the question here is only
    whether *this* action opened something new.
    """
    return {id(w) for w in QApplication.topLevelWidgets() if w.isVisible()}


def _opened_since(before: set[int], expected: QWidget) -> list[str]:
    return [f"{type(w).__name__} {w.width()}x{w.height()} title={w.windowTitle()!r}"
            for w in QApplication.topLevelWidgets()
            if w.isVisible() and id(w) not in before and w is not expected]


def test_startups_analyze_opens_no_extra_window(app):
    """The reported case: one blank frame per startup entry."""
    from app.screens.startups import StartupsScreen

    screen = StartupsScreen()
    screen.set_settings_store(SettingsStore())
    screen.resize(1400, 900)
    screen.show()
    app.processEvents()
    before = _visible_windows()

    screen._analyze()          # detect, rebuild the list, kick off AI
    for _ in range(30):
        app.processEvents()

    stray = _opened_since(before, screen)
    screen.close()
    screen.deleteLater()
    app.processEvents()
    assert not stray, "Analyze left windows on screen:\n  " + "\n  ".join(stray)


def test_home_rebuild_opens_no_extra_window(app):
    """Home clears and rebuilds its whole dynamic area on every navigation."""
    from app.screens.home import HomeScreen

    state = ScanState()
    state.set_settings_store(SettingsStore())
    screen = HomeScreen()
    screen.set_scan_state(state)
    screen.resize(1400, 900)
    screen.show()
    app.processEvents()
    before = _visible_windows()

    for _ in range(3):
        screen.refresh()
        app.processEvents()

    stray = _opened_since(before, screen)
    screen.close()
    screen.deleteLater()
    app.processEvents()
    assert not stray, "Home rebuild left windows on screen:\n  " + "\n  ".join(stray)


def test_clearing_a_layout_hides_rather_than_unparents(app):
    """The property that makes the two screens above safe.

    A widget taken out of a layout must end up invisible *and* still owned by
    something. Unparenting satisfies the first and breaks the second, and that
    is precisely the bug: the orphan becomes a window.
    """
    from PySide6.QtWidgets import QVBoxLayout
    from app.screens.startups import _clear_layout

    host = QWidget()
    lay = QVBoxLayout(host)
    child = QWidget()
    lay.addWidget(child)
    host.resize(300, 200)
    host.show()
    app.processEvents()
    assert child.isVisible()

    _clear_layout(lay)
    app.processEvents()

    assert not child.isVisible(), "cleared widget still paints over its replacement"
    assert not child.isWindow(), "cleared widget was unparented into a window"

    host.close()
    host.deleteLater()
    app.processEvents()
