"""Changing the language must never destroy a running thread.

Reported: start deleting things in Quick Cleanup, switch to Settings, change
the language — the app vanishes. No traceback, no dialog.

Why: every long job is a QThread parented to the screen that started it
(``CleanupWorker(parent=self)``). Changing language rebuilds the whole shell
and deletes the outgoing widget tree, Qt destroys the still-running QThread
with its parent, and ``QThread::~QThread`` calls ``std::terminate``. Windows
reports 0xC0000409 (__fastfail), which is why nothing was logged.

The crash itself cannot be asserted in-process — it takes the interpreter with
it — so what is pinned here are the properties that prevent it: a busy screen
says so, stopping actually stops, and a thread that will not stop is disowned
rather than destroyed.
"""
import time

import pytest
from PySide6.QtCore import QThread

from app.config.settings_store import SettingsStore
from app.services.workers import orphaned_workers, stop_all, stop_worker


class _Interruptible(QThread):
    """Stops promptly when asked, like the real workers."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        deadline = time.time() + 5
        while not self._cancel and time.time() < deadline:
            self.msleep(10)


class _Stubborn(QThread):
    """Ignores cancellation — the case that must not be allowed to crash."""

    def cancel(self):
        pass

    def run(self):
        self.msleep(1500)


# ── stopping ──────────────────────────────────────────────────────


def test_a_cooperative_worker_is_actually_stopped(qapp):
    worker = _Interruptible()
    worker.start()
    assert worker.isRunning()
    assert stop_worker(worker, 3000) is True
    assert not worker.isRunning()


def test_a_stubborn_worker_is_disowned_rather_than_destroyed(qapp):
    """Its parent has to be free to die. Destroying the thread with it is the
    crash, so the thread is cut loose and kept alive instead."""
    from PySide6.QtWidgets import QWidget

    host = QWidget()
    worker = _Stubborn(parent=host)
    worker.start()
    assert worker.parent() is host

    stopped = stop_worker(worker, 100)      # far too short on purpose
    assert stopped is False
    assert worker.parent() is None, "still owned by a widget that is about to die"
    assert orphaned_workers() >= 1, "dropped the last reference to a live thread"

    worker.wait(4000)
    host.deleteLater()


def test_stopping_nothing_is_fine(qapp):
    assert stop_worker(None) is True
    assert stop_all(None, None) is True


def test_a_finished_worker_needs_no_stopping(qapp):
    worker = _Interruptible()
    worker.start()
    worker.cancel()
    worker.wait(3000)
    assert stop_worker(worker, 10) is True


# ── the screens report their own work ─────────────────────────────


@pytest.fixture
def quick(qapp):
    from app.screens.quick_cleanup import QuickCleanupScreen
    screen = QuickCleanupScreen()
    screen.set_settings_store(SettingsStore())
    yield screen
    screen.stop_background_work(3000)
    screen.close()
    screen.deleteLater()
    qapp.processEvents()


def test_quick_cleanup_reports_a_running_deletion(quick, qapp):
    """The exact state from the report: files are being removed."""
    assert quick.busy_reason() == ""

    quick._worker = _Interruptible(parent=quick)
    quick._worker.start()
    qapp.processEvents()

    assert quick.busy_reason() != "", "a running cleanup must block the rebuild"
    assert quick.stop_background_work(3000) is True
    assert quick.busy_reason() == ""


@pytest.mark.parametrize("screen_name", ["Analyze", "Startups", "QuickCleanup"])
def test_every_thread_owning_screen_answers_for_itself(qapp, screen_name):
    """MainWindow asks each screen; a screen that cannot answer is invisible
    to the guard and takes its threads down with the shell."""
    from app.screens.analyze import AnalyzeScreen
    from app.screens.quick_cleanup import QuickCleanupScreen
    from app.screens.startups import StartupsScreen

    cls = {"Analyze": AnalyzeScreen, "Startups": StartupsScreen,
           "QuickCleanup": QuickCleanupScreen}[screen_name]
    screen = cls()
    assert callable(getattr(screen, "busy_reason", None))
    assert callable(getattr(screen, "stop_background_work", None))
    assert screen.busy_reason() == ""
    assert screen.stop_background_work(100) is True
    screen.close()
    screen.deleteLater()
    qapp.processEvents()


# ── the shell defers instead of rebuilding ────────────────────────


class _FakeScreen:
    def __init__(self, reason=""):
        self._reason = reason
        self.stopped = False

    def busy_reason(self):
        return self._reason

    def stop_background_work(self, timeout_ms=3000):
        self.stopped = True
        return True


def test_the_shell_finds_a_busy_screen(qapp):
    from app.main import PodbyeWindow

    window = PodbyeWindow.__new__(PodbyeWindow)      # no real UI needed
    window._screens = {"Quick Cleanup": _FakeScreen("a cleanup is removing files"),
                       "Home": _FakeScreen()}
    assert window._busy_reason() == "a cleanup is removing files"


def test_the_shell_reports_idle_when_nothing_runs(qapp):
    from app.main import PodbyeWindow

    window = PodbyeWindow.__new__(PodbyeWindow)
    window._screens = {"Home": _FakeScreen(), "Analyze": _FakeScreen()}
    assert window._busy_reason() == ""


def test_the_shell_stops_every_screen_before_teardown(qapp):
    from app.main import PodbyeWindow

    window = PodbyeWindow.__new__(PodbyeWindow)
    screens = {"a": _FakeScreen(), "b": _FakeScreen()}
    window._screens = screens
    assert window._stop_all_background_work(100) is True
    assert all(s.stopped for s in screens.values())
