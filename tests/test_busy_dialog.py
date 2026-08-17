"""The modal used to keep 'Open Findings' responsive while a session loads.

The two things that must never happen: returning the wrong value, and hanging.
The hang is the subtle one — if the worker finishes before exec() starts, a
naive implementation shows a dialog that nothing is left to close.
"""
import time

from app.widgets.progress import BusyDialog, run_busy


def test_returns_the_workers_result(qapp):
    assert run_busy(None, "Working…", lambda: {"session_id": "s1"}) == {"session_id": "s1"}


def test_fast_work_returns_without_showing_anything(qapp, monkeypatch):
    """A small session must not flash a modal open and shut."""
    shown = []
    monkeypatch.setattr(BusyDialog, "exec",
                        lambda self: shown.append(self) or 0)
    assert run_busy(None, "Working…", lambda: 42) == 42
    assert not shown, "showed a dialog for instant work"


def test_slow_work_shows_the_dialog_and_still_returns(qapp, monkeypatch):
    shown = []
    real_exec = BusyDialog.exec
    monkeypatch.setattr(BusyDialog, "exec",
                        lambda self: (shown.append(self), real_exec(self))[1])
    assert run_busy(None, "Working…", lambda: (time.sleep(0.5), "done")[1]) == "done"
    assert shown, "no dialog for work that takes half a second"


def test_work_finishing_before_exec_does_not_hang(qapp):
    """The race, forced: with a 0 ms threshold the worker always wins.

    wait(0) reports 'still running', so the dialog is shown — by which time the
    worker has already finished. Only the queued accept() closes it.
    """
    dlg = BusyDialog("Working…", None)
    assert dlg.run(lambda: "ok", show_after_ms=0) == "ok"


def test_escape_cannot_dismiss_the_dialog(qapp):
    """Closing it early would leave the worker writing into a dead dialog."""
    dlg = BusyDialog("Working…", None)
    dlg.reject()
    assert not dlg.isVisible() and dlg.result() == 0


def test_a_failing_worker_reports_none_instead_of_crashing(qapp):
    def boom():
        raise OSError("disk gone")

    assert run_busy(None, "Working…", boom) is None
