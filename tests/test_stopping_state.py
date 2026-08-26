"""Stopping a scan is a state, and starting work over running work asks first.

`halt()` only sets a flag — the worker notices it between directory entries,
which on a slow or very wide directory is not immediate. The button used to
jump straight back to "Start scan", enabled, while that thread was still
alive: it reported a finished stop that had not finished, and invited a second
scan on top of the first, which `_start_scan` did nothing to prevent.

The folder button beside it already waits for `_on_scan_finished`, because
"opening the folder dialog while the worker thread is still alive causes a
freeze on slow/large drives". Starting another scan is the worse version of
that, and it was the one left unguarded.
"""
import pytest
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication

from app.state.scan_state import ScanState


class _FakeWorker(QThread):
    """A real thread the test can hold open and release.

    Not a plain stub: the screen's own teardown calls stop_background_work on
    whatever is in _worker, which reaches for requestInterruption() and wait().
    A duck-typed object without them raises out of a fixture teardown and takes
    unrelated tests with it.
    """

    def __init__(self, running=True):
        super().__init__()
        self._go = running
        self.halted = False
        if running:
            self.start()
            while not self.isRunning():
                QApplication.processEvents()

    def run(self):
        while self._go and not self.isInterruptionRequested():
            self.msleep(2)

    def halt(self):
        self.halted = True

    def cancel(self):
        self.release()

    def release(self):
        self._go = False
        self.wait(2000)


@pytest.fixture
def screen(qapp):
    from app.screens.analyze import AnalyzeScreen
    scr = AnalyzeScreen(scan_state=ScanState())
    yield scr
    worker = getattr(scr, "_worker", None)
    if isinstance(worker, _FakeWorker):
        worker.release()
    try:
        scr.stop_background_work(1000)
    except Exception:
        pass
    scr.close()
    scr.deleteLater()
    qapp.processEvents()


def test_stopping_says_so_while_the_worker_winds_down(screen):
    screen._worker = _FakeWorker(running=True)
    screen._scan_active = True
    screen._stop_scan()
    assert screen._btn_scan.text() == "Stopping…"
    assert screen._btn_scan.isEnabled() is False


def test_the_button_returns_to_start_once_the_worker_is_gone(screen):
    screen._worker = _FakeWorker(running=True)
    screen._scan_active = True
    screen._stop_scan()
    screen._worker.release()
    screen._reset_scan_button()          # what _on_scan_finished calls
    assert screen._btn_scan.text() == "Start scan"
    assert screen._btn_scan.isEnabled() is True


def test_stopping_an_already_finished_scan_does_not_stick(screen):
    """No worker left: there is nothing to wait for, so do not say there is."""
    screen._worker = None
    screen._scan_active = True
    screen._stop_scan()
    assert screen._btn_scan.text() == "Start scan"
    assert screen._btn_scan.isEnabled() is True


def test_a_second_scan_cannot_start_while_the_first_is_alive(screen, tmp_path):
    screen._selected_folder = str(tmp_path)
    screen._worker = _FakeWorker(running=True)
    before = screen._worker
    screen._start_scan()
    assert screen._worker is before, "a second ScanWorker was created"


def test_a_dead_worker_wrapper_does_not_block_the_next_scan(screen):
    """A destroyed QThread raises through its wrapper; that must read as
    'not running', not as 'still running forever'."""
    class _Dead:
        def isRunning(self):
            raise RuntimeError("Internal C++ object already deleted")
    screen._worker = _Dead()
    assert screen._scan_worker_alive() is False


def test_the_header_stops_saying_running_while_it_stops(screen):
    """The header read "Adaptive scan running" directly above a button reading
    "Stopping…" — and only _on_scan_finished corrected it, which by definition
    has not run yet."""
    screen._sub.setText("Adaptive scan running")
    screen._worker = _FakeWorker(running=True)
    screen._scan_active = True
    screen._stop_scan()
    assert "running" not in screen._sub.text().lower()
    assert "stopping" in screen._sub.text().lower()
