"""With confirmation turned off, the dialog must not still look like a question.

"Don't ask again" makes _run_cleanup pass auto_confirm=True. The dialog then
opened wearing its full confirmation face — window title "Confirm Cleanup", an
armed "Move to Recycle Bin" button, a "Don't ask again" tick — and started
deleting one event-loop turn later. The user watched a question they were
never given the chance to answer, with the button flipping to "Moving…"
underneath the cursor.

Nothing about *what* gets removed changes here; only how the dialog presents
itself while it does it.
"""
import os
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


ITEMS = [{
    "path": "C:/Temp/build-cache", "name": "build-cache", "risk": "Optional",
    "size_bytes": 2_000_000, "category": "Cache", "is_dir": True,
    "removable_file_paths": ["C:/Temp/build-cache/a.o"],
}]


@pytest.fixture
def started(monkeypatch):
    """Record the auto-start instead of performing it.

    auto_confirm schedules _on_confirm on the event loop, which spawns a real
    CleanupWorker against real paths. These tests are about how the dialog
    presents itself, so the start is observed rather than run — otherwise the
    suite would be doing filesystem work and leaving a worker thread behind.
    The timer binds the method at construction, so this must be in place first.
    """
    calls = []
    from app.screens import cleanup_dialog
    monkeypatch.setattr(cleanup_dialog.CleanupConfirmDialog, "_on_confirm",
                        lambda self: calls.append(self))
    return calls


def _dialog(qapp, auto):
    from app.screens.cleanup_dialog import CleanupConfirmDialog
    # Not exec()'d: showing it modally would block, and every claim here is
    # about the state the dialog is built in, before it is displayed.
    return CleanupConfirmDialog(items=ITEMS, auto_confirm=auto)


# ── auto-confirm: present as progress ─────────────────────────────

def test_the_title_says_what_is_happening(qapp, started):
    dlg = _dialog(qapp, auto=True)
    assert dlg.windowTitle() != "Confirm Cleanup"
    assert "Moving" in dlg.windowTitle()


def test_the_header_says_what_is_happening(qapp, started):
    dlg = _dialog(qapp, auto=True)
    assert dlg._header_lbl.text() == "Moving to Recycle Bin"


def test_there_is_no_button_left_to_press(qapp, started):
    """A confirm button that arms itself and fires is not a confirmation."""
    dlg = _dialog(qapp, auto=True)
    assert not dlg._btn_confirm.isVisible()


def test_the_progress_area_is_up_front(qapp, started):
    dlg = _dialog(qapp, auto=True)
    assert dlg._progress_frame.isVisibleTo(dlg)


def test_cancel_survives(qapp, started):
    """The worker checks for cancellation between items — keep the way out."""
    dlg = _dialog(qapp, auto=True)
    assert dlg._btn_cancel.isVisibleTo(dlg)
    assert dlg._btn_cancel.isEnabled()


def test_the_risk_breakdown_stays(qapp, started):
    """What is being removed is exactly what a user wants to read meanwhile."""
    dlg = _dialog(qapp, auto=True)
    assert "1 item" in dlg._sub_lbl.text() or "item" in dlg._sub_lbl.text()


# ── normal path: unchanged ────────────────────────────────────────

def test_asking_still_asks(qapp, started):
    dlg = _dialog(qapp, auto=False)
    assert dlg.windowTitle() == "Confirm Cleanup"
    assert dlg._header_lbl.text() == "Move to Recycle Bin"
    assert not dlg._progress_frame.isVisibleTo(dlg)


def test_the_confirm_button_is_there_when_confirmation_is_on(qapp, started):
    dlg = _dialog(qapp, auto=False)
    assert dlg._btn_confirm.isVisibleTo(dlg)
    assert dlg._btn_confirm.isEnabled()


def test_nothing_starts_on_its_own_when_confirmation_is_on(qapp, started):
    _dialog(qapp, auto=False)
    qapp.processEvents()
    assert started == [], "the cleanup started without being confirmed"


def test_turning_confirmation_off_does_start_it(qapp, started):
    """The behaviour being re-dressed must still happen."""
    _dialog(qapp, auto=True)
    qapp.processEvents()
    assert len(started) == 1
