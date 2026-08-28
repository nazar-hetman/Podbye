"""Emptying the Recycle Bin: off the UI thread, and explicitly not cancellable.

SHEmptyRecycleBinW is a synchronous shell call with no cancel handle. Running
it from a click handler froze the window for as long as the delete took; the
interesting part is that it does not fit workers.py's contract, whose rule is
"cancel, wait a few seconds, orphan it if it will not stop". Two of those
three are unavailable here, so this is modelled as an operation the app is
busy with rather than as a worker something can stop.
"""
import time

import pytest

import app.services.bin_emptier as be
import app.services.recycle_bin as rb


@pytest.fixture(autouse=True)
def _clean_emptier(qapp):
    """Never let one test's worker leak into the next."""
    be.wait()
    be.reset_for_tests()
    yield
    be.wait()
    be.reset_for_tests()


@pytest.fixture
def slow_shell(monkeypatch):
    """Stand in for the shell call. Nothing is actually deleted."""
    state = {"calls": 0, "finished": False}

    def _empty(drive=None):
        state["calls"] += 1
        time.sleep(0.3)
        state["finished"] = True
        return True, "Recycle Bin emptied."

    monkeypatch.setattr(rb, "empty_recycle_bin", _empty)
    return state


# -- lifecycle ----------------------------------------------------

def test_it_runs_off_the_calling_thread(slow_shell, qapp):
    """The point of the exercise: start returns before the work is done."""
    started = time.time()
    assert be.start() is True
    assert time.time() - started < 0.2, "start() blocked on the shell call"
    assert be.is_emptying()
    be.wait()
    assert slow_shell["finished"]


def test_a_second_start_is_refused(slow_shell, qapp):
    assert be.start() is True
    assert be.start() is False
    be.wait()
    assert slow_shell["calls"] == 1


def test_busy_reason_is_empty_when_idle(qapp):
    assert be.busy_reason() == ""
    assert not be.is_emptying()


def test_busy_reason_names_the_operation_while_it_runs(slow_shell, qapp):
    be.start()
    assert "Recycle Bin" in be.busy_reason()
    be.wait()
    assert be.busy_reason() == ""


def test_wait_has_no_deadline_by_default(slow_shell, qapp):
    """timeout_ms is a cancellation budget elsewhere - stop, or be cut loose.
    Neither half is available for a call with no cancel handle, so a timeout
    here would only mean "carry on tearing down while the shell is deleting"."""
    be.start()
    began = time.time()
    assert be.wait() is True
    assert time.time() - began >= 0.25, "wait() returned before the call finished"
    assert not be.is_emptying()


def test_wait_on_an_idle_emptier_returns_immediately(qapp):
    began = time.time()
    assert be.wait() is True
    assert time.time() - began < 0.1


def test_finishing_is_broadcast(slow_shell, qapp):
    seen = []
    be.signaller().finished.connect(lambda ok, msg: seen.append((ok, msg)))
    be.start()
    be.wait()
    for _ in range(5):
        qapp.processEvents()
    assert seen and seen[0][0] is True


def test_a_failing_shell_call_is_reported_not_raised(monkeypatch, qapp):
    monkeypatch.setattr(rb, "empty_recycle_bin",
                        lambda drive=None: (_ for _ in ()).throw(OSError("nope")))
    seen = []
    be.signaller().finished.connect(lambda ok, msg: seen.append((ok, msg)))
    be.start()
    be.wait()
    for _ in range(5):
        qapp.processEvents()
    assert seen and seen[0][0] is False
    assert not be.is_emptying()


# -- ownership ----------------------------------------------------

def test_the_worker_belongs_to_no_widget(slow_shell, qapp):
    """A widget-owned thread dies with its widget, which is the crash
    workers.py exists to prevent - and the one job that cannot be interrupted
    is the worst candidate for that ownership."""
    be.start()
    assert be._worker is not None
    assert be._worker.parent() is None
    be.wait()


def test_it_is_not_registered_as_a_cancellable_worker(slow_shell, qapp):
    """workers.stop_all would cancel and orphan it; there is nothing to cancel
    and orphaning is what takes the process down."""
    be.start()
    assert not hasattr(be._worker, "cancel")
    be.wait()
