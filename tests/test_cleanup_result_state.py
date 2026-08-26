"""When the cleanup finishes, the dialog shows the outcome — and only that.

It used to append the result under the confirmation, so a finished dialog held
"203 items · 13.1 GB will be sent to the Recycle Bin" directly above "135
moved · 42 issues · 26 skipped": the plan and the outcome, contradicting each
other, in one scroll. And "42 unexpected issue(s) need attention" named none of
the 42, which is the part a user can actually do something about.
"""
import pytest
from PySide6.QtWidgets import QScrollArea

from app.screens.cleanup_dialog import CleanupConfirmDialog
from app.services.cleanup_engine import CleanupResult
from app.widgets.controls import ElidedLabel


def _item(path, risk="Optional", size=1024):
    return {"path": path, "name": path.rsplit("/", 1)[-1], "category": "Cache & Temp",
            "risk": risk, "size_bytes": size, "reclaimable_bytes": size,
            "entity_type": "cache_folder", "actionability": "recycle"}


@pytest.fixture
def dlg(qapp):
    items = [_item(f"C:/tmp/f{i}", risk="Review") for i in range(4)]
    d = CleanupConfirmDialog(items)
    d.show()
    qapp.processEvents()
    yield d
    d.close()
    d.deleteLater()
    qapp.processEvents()


def _finish(dlg, qapp, **kw):
    result = CleanupResult(**kw)
    dlg._armed = dlg._armed_targets()
    dlg._on_finished(result)
    qapp.processEvents()
    return result


def _visible_text(dlg):
    out = []
    for w in dlg.findChildren(object):
        text = getattr(w, "text", None)
        if callable(text) and getattr(w, "isVisible", lambda: False)():
            try:
                out.append(text())
            except TypeError:
                pass
    return "\n".join(t for t in out if isinstance(t, str))


def test_the_plan_is_gone_once_it_has_happened(dlg, qapp):
    assert "will be sent" in dlg._sub_lbl.text()
    _finish(dlg, qapp, succeeded=["C:/tmp/f0"], total_bytes_freed=1024)
    assert "will be sent" not in _visible_text(dlg)


def test_the_risk_breakdown_does_not_outlive_the_operation(dlg, qapp):
    """It describes a selection that no longer exists."""
    before = [w for w in dlg._confirm_only if w.isVisible()]
    assert before, "nothing was tagged as confirmation-only"
    _finish(dlg, qapp, succeeded=["C:/tmp/f0"], total_bytes_freed=1024)
    assert not any(w.isVisible() for w in dlg._confirm_only)


def test_the_header_states_the_outcome(dlg, qapp):
    _finish(dlg, qapp, succeeded=["C:/tmp/f0", "C:/tmp/f1"], total_bytes_freed=2048)
    assert dlg._header_lbl.text() == "Cleanup complete"
    assert "2" in dlg._sub_lbl.text()


def test_a_run_with_failures_says_so_in_the_header(dlg, qapp):
    _finish(dlg, qapp, succeeded=["C:/tmp/f0"], failed=["C:/tmp/f1"],
            total_bytes_freed=1024)
    assert dlg._header_lbl.text() == "Cleanup finished with issues"


def test_failures_are_named_paths_not_a_count(dlg, qapp):
    _finish(dlg, qapp, succeeded=["C:/tmp/f0"],
            in_use=["C:/tmp/f1"], failed=["C:/tmp/f2"],
            skipped_protected=["C:/tmp/f3"],
            errors_by_path={"C:/tmp/f2": "WinError 5: access denied"},
            total_bytes_freed=1024)
    paths = [p for _reason, p, _detail in dlg._issues]
    assert set(paths) == {"C:/tmp/f1", "C:/tmp/f2", "C:/tmp/f3"}
    assert dlg._issues_frame.isVisible()


def test_the_issue_list_is_ordered_worst_first(dlg, qapp):
    """A protected skip is Podbye working correctly and can be scrolled past;
    an unexplained failure cannot."""
    _finish(dlg, qapp, succeeded=["C:/tmp/f0"], not_recycled=["C:/tmp/f0"],
            in_use=["C:/tmp/f1"], failed=["C:/tmp/f2"],
            skipped_protected=["C:/tmp/f3"], total_bytes_freed=1024)
    assert [r for r, _p, _d in dlg._issues] == [
        "Deleted permanently", "Failed", "In use", "Protected"]


def test_each_issue_carries_the_reason_it_failed(dlg, qapp):
    _finish(dlg, qapp, failed=["C:/tmp/f2"],
            errors_by_path={"C:/tmp/f2": "WinError 5: access denied"})
    reason, path, detail = dlg._issues[0]
    assert reason == "Failed"
    assert detail == "WinError 5: access denied"
    row = next(r for r in dlg._issues_body.findChildren(ElidedLabel)
               if path in r.full_text())
    assert "access denied" in row.toolTip()


def test_a_permanent_deletion_is_reported_as_permanent(dlg, qapp):
    """These are gone for good, so they must not sit in the recycled count."""
    _finish(dlg, qapp, succeeded=["C:/tmp/f0"], not_recycled=["C:/tmp/f0"],
            total_bytes_freed=1024)
    assert dlg._issues[0][0] == "Deleted permanently"
    assert "cannot be restored" in dlg._result_lbl.text()


def test_the_issue_list_starts_folded(dlg, qapp):
    _finish(dlg, qapp, failed=["C:/tmp/f2"])
    assert dlg._issues_scroll.isVisible() is False
    assert "1 item(s) need attention" in dlg._btn_issues.text()
    dlg._toggle_issues()
    qapp.processEvents()
    assert dlg._issues_scroll.isVisible() is True


def test_a_clean_run_shows_no_issue_list_at_all(dlg, qapp):
    _finish(dlg, qapp, succeeded=["C:/tmp/f0"], total_bytes_freed=1024)
    assert dlg._issues == []
    assert dlg._issues_frame.isVisible() is False


def test_the_paths_can_be_copied_out(dlg, qapp):
    _finish(dlg, qapp, in_use=["C:/tmp/f1"], failed=["C:/tmp/f2"],
            errors_by_path={"C:/tmp/f2": "WinError 32"})
    dlg._copy_issue_list()
    text = qapp.clipboard().text()
    assert "C:/tmp/f1" in text and "C:/tmp/f2" in text
    assert "WinError 32" in text


def test_the_close_button_is_visible_after_an_auto_confirmed_run(qapp):
    """_present_as_progress hides the confirm button on the way in; the
    finished dialog has to put it back, not merely re-enable it."""
    d = CleanupConfirmDialog([_item("C:/tmp/f0")], auto_confirm=False)
    d.show()
    qapp.processEvents()
    d._present_as_progress()
    d._armed = d._armed_targets()
    d._on_finished(CleanupResult(succeeded=["C:/tmp/f0"], total_bytes_freed=1))
    qapp.processEvents()
    assert d._btn_confirm.isVisible()
    assert d._btn_confirm.text() == "Close"
    d.close()
    d.deleteLater()
    qapp.processEvents()


def test_the_finished_dialog_has_no_leftover_scroll_from_the_review_list(dlg, qapp):
    """The review list is part of the plan; a scrollable ghost of it under the
    result is the same stacked-states bug in a different shape."""
    _finish(dlg, qapp, succeeded=["C:/tmp/f0"], total_bytes_freed=1024)
    review_scrolls = [s for s in dlg.findChildren(QScrollArea)
                      if s is not dlg._issues_scroll and s.isVisible()]
    assert review_scrolls == []


def test_the_explanation_is_actually_on_screen(dlg, qapp):
    """It was set on a label parented inside the progress frame, so it
    inherited that frame's visibility and never appeared."""
    _finish(dlg, qapp, succeeded=["C:/tmp/f0"], failed=["C:/tmp/f1"],
            total_bytes_freed=1024)
    assert dlg._result_lbl.text().strip()
    assert dlg._result_lbl.isVisible()
    assert dlg._result_lbl.height() > 10
