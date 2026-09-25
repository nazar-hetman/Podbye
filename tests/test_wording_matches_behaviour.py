"""What the screens say must be what the code does.

Each claim below was stronger than the implementation behind it:

* Quick Cleanup's result said "TOTAL FREED" for files moved to the Recycle
  Bin - which frees nothing until the bin is emptied, as the same screen says
  a few lines further down.
* "Fully recoverable" was printed whatever the result, including a run where
  an item was removed for good.
* Empty Recycle Bin said "everything Podbye cleaned is in here" while emptying
  the whole bin - the user's own deletions included.
* The Startups header said "Startup controls update Podbye state", about a
  Disable button that no longer exists.
* "Change in Task Manager" was offered for scheduled tasks, which Task Manager
  does not list.
* Analyze named its last stage "AI classification"; the AI only explains, it
  never changes a verdict.
"""
import time

import pytest

import app.services.recycle_bin as rb
from app.services.cleanup_engine import CleanupResult
from app.services.quick_cleanup_detector import QuickCleanupCategory


@pytest.fixture
def qc_screen(qapp, monkeypatch):
    monkeypatch.setattr(rb, "recycle_bin_status", lambda drive=None: (5 * 10 ** 9, 800))
    from app.screens.quick_cleanup import QuickCleanupScreen, _READY
    s = QuickCleanupScreen()
    s._on_category_found(QuickCleanupCategory(
        key="user_temp", label="Temp Files", subtitle="C:/t", paths=["C:/t/a"],
        size_bytes=10 ** 6, file_count=1))
    s._state = _READY
    s._on_scan_done()
    yield s
    s.deleteLater()


def _finish(screen, result):
    screen._cleaning_rows = list(screen._rows)
    screen._cleanup_start_time = time.monotonic()
    screen._on_cleanup_done(result)


def test_quick_cleanup_does_not_call_a_move_freed(qc_screen, monkeypatch):
    monkeypatch.setattr(qc_screen, "_write_history", lambda r: None)
    _finish(qc_screen, CleanupResult(succeeded=["C:/t/a"],
                                     bytes_by_path={"C:/t/a": 10 ** 6},
                                     total_bytes_freed=10 ** 6))
    assert qc_screen._total_hdr.text() == "MOVED TO RECYCLE BIN"
    assert "freed" not in qc_screen._subtitle_lbl.text().lower()


def test_recoverability_is_not_promised_after_a_permanent_removal(qc_screen, monkeypatch):
    monkeypatch.setattr(qc_screen, "_write_history", lambda r: None)
    _finish(qc_screen, CleanupResult(succeeded=["C:/t/a"], not_recycled=["C:/t/a"],
                                     bytes_by_path={"C:/t/a": 10 ** 6},
                                     total_bytes_freed=10 ** 6))
    assert not qc_screen._recovery_lbl.isVisibleTo(qc_screen)


def test_emptying_the_bin_says_it_empties_all_of_it(qc_screen, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: asked.append(a) or QMessageBox.No)
    qc_screen._on_empty_recycle_bin()
    text = asked[0][2]
    assert "whole Recycle Bin" in text
    assert "deleted yourself" in text
    assert "everything Podbye cleaned is in here" not in text


# ── Startups ──────────────────────────────────────────────────────

def _entry(source):
    from app.models.startup_entry import StartupEntry
    return StartupEntry(name="Updater", command="C:/x/u.exe", path="C:/x/u.exe",
                        source=source, source_label=source, enabled=True,
                        publisher="", risk="Review", risk_reason="", impact="")


def test_a_scheduled_task_is_sent_to_task_scheduler(qapp):
    from app.screens.startups import StartupInspectorPanel
    panel = StartupInspectorPanel()
    fired = []
    panel.task_scheduler_requested.connect(lambda: fired.append("scheduler"))
    panel.task_manager_requested.connect(lambda: fired.append("manager"))

    panel.set_entry(_entry("scheduled_task"))
    assert panel._tm_btn.text() == "Change in Task Scheduler ↗"
    panel._tm_btn.click()
    assert fired == ["scheduler"]


def test_a_run_key_entry_is_still_sent_to_task_manager(qapp):
    from app.screens.startups import StartupInspectorPanel
    panel = StartupInspectorPanel()
    fired = []
    panel.task_scheduler_requested.connect(lambda: fired.append("scheduler"))
    panel.task_manager_requested.connect(lambda: fired.append("manager"))

    panel.set_entry(_entry("run_hkcu"))
    assert panel._tm_btn.text() == "Change in Task Manager ↗"
    panel._tm_btn.click()
    assert fired == ["manager"]


def test_the_startups_header_describes_what_the_screen_does(qapp):
    from PySide6.QtWidgets import QLabel
    from app.screens.startups import StartupsScreen
    screen = StartupsScreen()
    texts = [w.text() for w in screen.findChildren(QLabel)]
    assert not any("Startup controls update Podbye state" in t for t in texts)
    assert any("Podbye explains startup entries" in t for t in texts)
    screen.deleteLater()


# ── Analyze ───────────────────────────────────────────────────────

def test_analyze_does_not_call_explanations_classification():
    from app.screens import analyze
    import inspect
    src = inspect.getsource(analyze)
    assert 'tr("AI classification")' not in src
    assert 'tr("AI explanations")' in src
