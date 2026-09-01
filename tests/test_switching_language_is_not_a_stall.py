"""Changing the interface language should not feel like the app hung.

Reported as "Findings and Quick Cleanup after switching languages sometimes a
bit freezing (not critically) but I can see it". Measured end to end on this
machine, with the shell rebuilt and the current screen re-shown:

    while on Findings        2.6 s
    while on Quick Cleanup   4.2 s

The switch rebuilds the whole shell — ``_build_ui()`` constructs all seven
screens — and then calls ``_apply_theme(self._current_theme)``. That second
step was the larger half and almost all of it was wasted: the *theme* has not
changed, so the QSS handed to Qt was byte-for-byte the string already set, and
``QApplication.setStyleSheet`` re-polishes every live widget regardless.

Worse, it ran while the outgoing shell was still alive — ``deleteLater()``
only posts the delete — so it restyled two complete widget trees. Measured:
719 widgets before the switch, 1,515 during the restyle.

Skipping an identical stylesheet takes that step from 1,323 ms to 125 ms and
roughly halves the switch. What is left is the rebuild itself (~950 ms); the
lever there is lazy screen construction, which ``_apply_theme``'s own comment
already identifies and which is not attempted here.

One fix was tried and reverted: flushing the deferred delete inside
``_build_ui`` so the restyle would only see the new tree. It kills the
process. ``_build_ui`` is reached from the Settings screen's own
``settings_saved`` signal, so the outgoing tree holds the widget whose handler
is still on the stack, and deleting it synchronously destroys the object
mid-dispatch. The delete stays deferred.
"""
import pytest
from PySide6.QtWidgets import QApplication

from app.themes.theme_manager import build_qss


def _dispose(win, qapp):
    """Take the window down now, not whenever the collector gets to it.

    deleteLater() only *posts* a DeferredDelete, and processEvents() outside a
    running event loop does not deliver those — so a window built here stayed
    alive until Python collected its wrapper, and PySide then destroyed the
    C++ tree from inside the garbage collector. That is an access violation
    with no traceback, landing on whatever unrelated test the GC happened to
    run under: the full suite died in ast.parse inside a locale test.

    tests/test_no_clipped_text.py documents the same failure from the same
    cause. A PodbyeWindow is the heaviest tree in the app, so it is disposed
    of explicitly: stop the threads, close so each screen's closeEvent runs,
    then actually deliver the delete.
    """
    from PySide6.QtCore import QCoreApplication, QEvent

    from app.i18n import set_language

    # set_language() is process-global and these tests change it on purpose.
    # Leaving it changed made every later test in the run read a Ukrainian UI:
    # 57 failures across 20 files, all of the shape `assert '3 items' in
    # '3 елем. у цьому додатку'`. This file sorts first, so it poisoned the
    # whole suite.
    set_language("English")
    win._stop_all_background_work()
    win.close()
    win.deleteLater()
    qapp.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    qapp.processEvents()


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    """A real shell. Deliberately never shown.

    Showing it runs each screen's showEvent — Quick Cleanup starts a detector
    thread from its own — and this file is about the rebuild, not about what
    the screens do afterwards.
    """
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from app.main import PodbyeWindow

    win = PodbyeWindow()
    win.resize(1400, 900)
    for _ in range(4):
        qapp.processEvents()
    yield win
    _dispose(win, qapp)


def _stylesheet_calls(monkeypatch):
    """Count QApplication.setStyleSheet calls without suppressing them."""
    calls = []
    app = QApplication.instance()
    original = app.setStyleSheet
    monkeypatch.setattr(app, "setStyleSheet",
                        lambda qss: (calls.append(len(qss)), original(qss))[1])
    return calls


# ── the wasted restyle ────────────────────────────────────────────

def test_reapplying_the_same_theme_does_not_restyle_everything(window, monkeypatch):
    """The language-switch case: same theme, same string, nothing to do."""
    window._apply_theme("forest")
    calls = _stylesheet_calls(monkeypatch)

    window._apply_theme("forest")

    assert calls == [], "re-polished every widget for an identical stylesheet"


def test_a_real_theme_change_still_applies(window, monkeypatch):
    """The guard must not swallow the case the method exists for."""
    window._apply_theme("forest")
    calls = _stylesheet_calls(monkeypatch)

    window._apply_theme("amber")

    assert len(calls) == 1
    assert QApplication.instance().styleSheet() == build_qss("amber")


def test_switching_back_and_forth_keeps_working(window):
    for theme in ("forest", "amber", "mono", "forest"):
        window._apply_theme(theme)
        assert QApplication.instance().styleSheet() == build_qss(theme), theme


def test_the_theme_is_still_recorded_when_the_sheet_is_unchanged(window):
    """Skipping the restyle must not skip the bookkeeping around it."""
    window._apply_theme("amber")
    window._apply_theme("amber")

    assert window._current_theme == "amber"


# ── and the switch itself still leaves a correct shell ────────────

def test_a_language_switch_leaves_the_stylesheet_intact(window, qapp):
    """Widgets built after an application stylesheet is set inherit it when
    they are polished — which is why the re-apply was redundant rather than
    load-bearing. If that were ever untrue the new shell would come up
    unstyled, so it is checked rather than assumed."""
    window._apply_theme("forest")

    window._apply_language_change("Ukrainian")
    for _ in range(4):
        qapp.processEvents()

    assert QApplication.instance().styleSheet() == build_qss("forest")
    from app.i18n import get_language
    assert get_language() == "Ukrainian"


def test_the_rebuilt_shell_has_every_screen(window, qapp):
    before = set(window._screens)

    window._apply_language_change("German")
    for _ in range(4):
        qapp.processEvents()

    assert set(window._screens) == before
    assert window._stack.count() == len(before)


def test_the_outgoing_shell_is_not_deleted_synchronously(window, qapp):
    """It is reached from a Settings signal, so a synchronous delete destroys
    the sender mid-dispatch and takes the process with it. Asserted as "the
    old tree is still alive when _build_ui returns", which is the property
    that makes it safe.
    """
    old_central = window.centralWidget()

    window._build_ui()

    # Still a live C++ object: touching it would raise RuntimeError if the
    # delete had already been delivered.
    assert old_central.isHidden()
    assert old_central.parent() is window
