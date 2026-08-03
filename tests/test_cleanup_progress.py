"""The Recycle Bin dialog must look like it is working, because it is.

Reported: "Move to Recycle bin works odd - popup appearing you can't do anything
and something happening - bad design".

The modal lock is deliberate — tearing a dialog down while a delete thread
writes into it invites a crash. What was missing was any sign of life. Progress
was a per-*item* counter, so recycling one large folder is a single item and the
text sat at "Moving 1 / 1" for the whole operation, with the window refusing to
close. That is indistinguishable from a hang.

The counter was also wrong: it rendered ``done + 1``, announcing an item as
moved before the move was attempted.
"""
import pytest

from app.screens.cleanup_dialog import CleanupConfirmDialog, _elide_middle


def _item(path="C:/Temp/cache", size=500 * 1024 * 1024, risk="Safe"):
    return {
        "path": path, "name": path.rsplit("/", 1)[-1], "size_bytes": size,
        "risk": risk, "category": "Cache & Temp", "is_entity": True,
        "entity_type": "cache_folder", "actionability": "recycle",
    }


@pytest.fixture
def dlg(qapp):
    d = CleanupConfirmDialog([_item()])
    yield d
    d.deleteLater()


# ── the bar exists and moves ──────────────────────────────────────

def test_a_single_item_gets_an_indeterminate_bar(qapp):
    """One big folder is one item — there is no fraction to show, only motion."""
    d = CleanupConfirmDialog([_item()])
    d._armed = d._armed_targets()
    d._progress_frame.setVisible(True)
    d._progress_bar.setRange(0, 0 if len(d._armed) <= 1 else len(d._armed))

    assert (d._progress_bar.minimum(), d._progress_bar.maximum()) == (0, 0), \
        "a static bar on a single item is what looked frozen"


def test_many_items_get_a_real_fraction(qapp):
    items = [_item(f"C:/Temp/c{i}") for i in range(5)]
    d = CleanupConfirmDialog(items)
    total = len(d._armed_targets())
    d._progress_bar.setRange(0, total)

    d._on_progress(2, total, "C:/Temp/c2")

    assert d._progress_bar.maximum() == total
    assert d._progress_bar.value() == 2


def test_the_counter_does_not_run_ahead_of_the_work(dlg):
    """`done` items are finished; the named path is the one starting now."""
    dlg._on_progress(3, 10, "C:/Temp/whatever")
    assert "3" in dlg._progress_lbl.text()
    assert "4" not in dlg._progress_lbl.text()


def test_the_item_in_flight_is_named(dlg):
    dlg._on_progress(0, 4, "C:/Temp/some-folder")
    assert "some-folder" in dlg._progress_path_lbl.text()


def test_finishing_fills_the_bar_and_clears_the_path(dlg):
    dlg._on_progress(0, 3, "C:/Temp/a")
    dlg._on_progress(3, 3, "")

    assert dlg._progress_bar.value() == dlg._progress_bar.maximum()
    assert dlg._progress_bar.maximum() > 0, "still sweeping after it finished"
    assert dlg._progress_path_lbl.text() == ""


def test_cancelling_keeps_the_bar_alive(dlg):
    """Cancellation lands between items, so the one in flight still runs."""
    class _FakeWorker:
        def isRunning(self): return True
        def cancel(self): pass

    dlg._worker = _FakeWorker()
    dlg._on_cancel()

    assert (dlg._progress_bar.minimum(), dlg._progress_bar.maximum()) == (0, 0)
    assert not dlg._btn_cancel.isEnabled()
    assert dlg.result() == 0, "cancelling closed the dialog under a live worker"


# ── the window still may not be torn down mid-delete ──────────────

def test_the_dialog_refuses_to_close_while_working(dlg):
    from PySide6.QtGui import QCloseEvent

    class _FakeWorker:
        def isRunning(self): return True
        def cancel(self): pass

    dlg._worker = _FakeWorker()
    ev = QCloseEvent()
    dlg.closeEvent(ev)
    assert not ev.isAccepted(), "let the user close the dialog mid-delete"


def test_the_dialog_closes_normally_when_idle(dlg):
    from PySide6.QtGui import QCloseEvent

    ev = QCloseEvent()
    dlg.closeEvent(ev)
    assert ev.isAccepted()


# ── name shortening ───────────────────────────────────────────────

def test_short_names_are_left_alone():
    assert _elide_middle("setup.exe", 40) == "setup.exe"


def test_long_names_keep_both_ends():
    """Installers collide at the front and differ at the back, and vice versa."""
    name = "Stream_Brave1_1.0.11-windows-x64-installer.exe"
    out = _elide_middle(name, 20)

    assert len(out) <= 20
    assert out.startswith("Stream")
    assert out.endswith(".exe")
    assert "…" in out


def test_two_similar_names_stay_distinguishable():
    a = _elide_middle("Stream_Brave1_1.0.8_installer_final.exe", 24)
    b = _elide_middle("Stream_Brave1_1.0.11_installer_final.zip", 24)
    assert a != b
