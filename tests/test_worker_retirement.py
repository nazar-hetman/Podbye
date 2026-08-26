"""A QThread must be joined before its reference is dropped or it is deleted.

`finished_scan` is emitted as the last statement *inside* `run()`, so the
thread has not returned when the slot connected to it executes. Deleting the
worker there destroys a QThread that is still running: `QThread::~QThread`
calls `std::terminate` and Windows reports 0xC0000409 — the process vanishes
with no traceback, nowhere near the click that caused it.

Reproduced on an ordinary sequence: scan a drive, stop it, scan again, close
the window. 3 runs out of 3 crashed without the join; 3 out of 3 survived with
it, across all three variants of the sequence.
"""
import pytest
from PySide6.QtCore import QThread

from app.services.workers import _ORPHANED, orphaned_workers, retire_worker


class _Worker(QThread):
    def __init__(self, spin=False, parent=None):
        super().__init__(parent)
        self._spin = spin
        self.cancelled = False

    def cancel(self):
        self.cancelled = True
        self._spin = False

    def run(self):
        while self._spin and not self.isInterruptionRequested():
            self.msleep(5)


@pytest.fixture(autouse=True)
def _clean_orphans():
    yield
    for worker in list(_ORPHANED):
        for name in ("release", "cancel"):
            stop = getattr(worker, name, None)
            if callable(stop):
                stop()
        worker.requestInterruption()
        worker.wait(2000)
    _ORPHANED.clear()


def test_retiring_none_is_harmless():
    retire_worker(None)


def test_a_finished_thread_is_joined(qapp):
    """The case that crashed: run() has returned, isRunning() is already False,
    and the thread has never been waited on."""
    worker = _Worker(spin=False)
    worker.start()
    while worker.isRunning():
        qapp.processEvents()
    retire_worker(worker)
    assert worker.isFinished()
    assert orphaned_workers() == 0, "a joined thread should not be orphaned"


def test_a_running_thread_is_cancelled_and_joined(qapp):
    worker = _Worker(spin=True)
    worker.start()
    while not worker.isRunning():
        qapp.processEvents()
    retire_worker(worker, timeout_ms=3000)
    assert worker.cancelled, "never asked it to stop"
    assert not worker.isRunning()
    assert orphaned_workers() == 0


def test_a_thread_that_will_not_stop_is_disowned_not_dropped(qapp):
    """Keeping the Python reference matters as much as the reparenting —
    dropping the last one destroys the C++ QThread, which is the same crash."""
    class _Stubborn(QThread):
        """Ignores both cancel() and interruption, like a thread stuck in a
        blocking syscall — the case the orphan list exists for."""

        def __init__(self):
            super().__init__()
            self._go = True

        def cancel(self):
            pass

        def run(self):
            while self._go:
                self.msleep(5)

        def release(self):
            self._go = False

    worker = _Stubborn()
    worker.start()
    while not worker.isRunning():
        qapp.processEvents()
    try:
        retire_worker(worker, timeout_ms=50)
        assert orphaned_workers() == 1, "a live thread was dropped instead of kept"
        assert worker.parent() is None, "still attached to a parent that may die"
    finally:
        worker.release()
        worker.wait(2000)


def test_a_dead_wrapper_does_not_raise():
    class _Dead:
        """A deleted QThread wrapper raises from every method, not just one."""

        def __getattr__(self, name):
            def _raise(*args, **kwargs):
                raise RuntimeError("Internal C++ object already deleted")
            return _raise

    retire_worker(_Dead())          # must not propagate


def test_the_scan_screen_retires_before_it_replaces_or_deletes():
    """Both places that drop the reference have to go through it."""
    import inspect
    from app.screens.analyze import AnalyzeScreen

    for name in ("_start_scan", "_on_scan_finished", "_on_dup_finished"):
        src = inspect.getsource(getattr(AnalyzeScreen, name))
        assert "retire_worker" in src, f"{name} drops a worker without joining it"
