"""What the screens do while the Recycle Bin is being emptied.

The operation cannot be cancelled, so the app's job is to say it is happening,
refuse the things that would race it, and wait for it at shutdown rather than
tearing down around it.
"""
import time

import pytest

import app.services.bin_emptier as be
import app.services.recycle_bin as rb
from app.screens.quick_cleanup import QuickCleanupScreen, _READY
from app.services.quick_cleanup_detector import QuickCleanupCategory


@pytest.fixture(autouse=True)
def _clean_emptier(qapp):
    be.wait()
    be.reset_for_tests()
    yield
    be.wait()
    be.reset_for_tests()


@pytest.fixture
def slow_shell(monkeypatch):
    def _empty(drive=None):
        time.sleep(0.3)
        return True, "Recycle Bin emptied."

    monkeypatch.setattr(rb, "empty_recycle_bin", _empty)
    monkeypatch.setattr(rb, "recycle_bin_status", lambda drive=None: (5 * 10 ** 9, 800))


@pytest.fixture
def screen(qapp, slow_shell):
    s = QuickCleanupScreen()
    s.resize(1400, 860)
    s.show()
    for key in ("temp", "browser"):
        s._on_category_found(QuickCleanupCategory(
            key=key, label=key.title(), subtitle="C:/" + key, paths=["C:/" + key],
            size_bytes=10 ** 8, file_count=100))
    s._state = _READY
    s._on_scan_done()
    qapp.processEvents()
    yield s
    be.wait()
    s.deleteLater()
    qapp.processEvents()


# -- the screen says it is happening ------------------------------

def test_the_bin_readout_becomes_a_state(screen, qapp):
    """Querying mid-empty returns a number that is already wrong, and a
    falling count invites a second click on a button already doing the job."""
    before = screen._bin_lbl.text()
    be.start()
    screen.refresh_recycle_bin()
    assert "Emptying" in screen._bin_lbl.text()
    assert screen._bin_lbl.text() != before


def test_the_empty_button_is_disabled_while_it_runs(screen, qapp):
    be.start()
    screen.refresh_recycle_bin()
    assert not screen._btn_empty_bin.isEnabled()


def test_the_readout_returns_when_it_finishes(screen, qapp):
    be.start()
    screen.refresh_recycle_bin()
    be.wait()
    for _ in range(5):
        qapp.processEvents()
    assert "Emptying" not in screen._bin_lbl.text()


# -- nothing may add to the bin meanwhile -------------------------

def test_the_clean_button_greys_out(screen, qapp):
    be.start()
    screen._update_summary()
    assert not screen._btn_clean.isEnabled()
    assert "Emptying" in screen._btn_clean.text()


def test_clicking_clean_starts_nothing(screen, qapp):
    """Filling and emptying the bin at once leaves two screens disagreeing
    about what is in there."""
    be.start()
    screen._on_clean_clicked()
    assert screen._worker is None


def test_the_clean_button_comes_back_afterwards(screen, qapp):
    be.start()
    screen._update_summary()
    be.wait()
    for _ in range(5):
        qapp.processEvents()
    screen._update_summary()
    assert screen._btn_clean.isEnabled()


def test_findings_refuses_to_recycle_meanwhile(qapp, slow_shell, monkeypatch):
    import app.screens.findings_dashboard as fd
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: QMessageBox.Ok))
    view = fd.CategoryDetailView()
    try:
        view._scan_state = object()
        be.start()
        assert view._run_cleanup([{"path": "C:/x"}]) is False
    finally:
        be.wait()
        view.deleteLater()
        qapp.processEvents()


# -- teardown waits, it does not cut it loose ---------------------

def test_busy_reason_reports_it(screen, qapp):
    be.start()
    assert "Recycle Bin" in screen.busy_reason()


def test_stop_background_work_waits_for_it(screen, qapp):
    """The 3s cancellable-worker timeout is the wrong instrument: there is
    nothing to cancel, and orphaning the thread is the crash rather than the
    escape from it."""
    be.start()
    began = time.time()
    screen.stop_background_work()
    assert time.time() - began >= 0.25
    assert not be.is_emptying()


def test_the_app_reports_it_as_busy(qapp, slow_shell, monkeypatch):
    """It is service-owned, so the app asks the service rather than a screen -
    closing the screen that started it must not end it."""
    import app.main as m

    be.start()
    try:
        assert be.busy_reason() in m.PodbyeWindow._busy_reason(_FakeWindow())
    finally:
        be.wait()


class _FakeWindow:
    """Just enough of the window for _busy_reason: no screens of its own."""
    _screens: dict = {}


def test_it_is_not_offered_as_something_the_user_can_stop(qapp, slow_shell):
    """_is_busy drives prompts that say "Continuing will stop it and discard
    the results it has collected so far". There is no honest way to say that
    about a shell call with no cancel handle, and the prompts it guards -
    opening a History session, re-running a scan - do not touch the bin.
    Closing still waits for it; a shell rebuild is still blocked by
    _busy_reason. It simply is not a thing to offer to interrupt.
    """
    import app.main as m

    be.start()
    try:
        assert m.PodbyeWindow._is_busy(_IdleWindow()) is False
    finally:
        be.wait()


class _IdleWindow:
    """Nothing of its own is running - only the emptier."""

    class _State:
        is_running = False

    _scan_state = _State()
    _ai_explainer = None
