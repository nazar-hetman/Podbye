"""Old results cannot delete on the strength of old verdicts.

A saved session carries every row's risk and actionability as they were when
it was scanned. Reopened from History, those rows are offered for cleanup
exactly as saved - so the classifier fix that stopped D:/Temp and Blog being
called Safe would not protect anyone reopening a scan made before it. And a
session reopened days later recycles whole folders whose contents may have
changed since anything looked at them.

Sessions now record the classifier version that produced them. Cleanup from
results made by an older classifier is refused with a request to scan again,
a resumed scan re-classifies instead of reusing old verdicts, and results
older than a day are confirmed before anything is moved.
"""
import time

import pytest

from app.services.entity_detector import CLASSIFIER_VERSION
from app.state import result_freshness
from app.state.result_freshness import (
    OUTDATED, STALE, USABLE, STALE_AFTER_SECONDS, cleanup_gate,
)
from app.state.scan_state import ScanState
from app.state.session_store import build_snapshot


def _snapshot(**kw):
    base = dict(session_id="s1", target="D:/", scan_mode="smart",
                status="completed", start_time=time.time(), scanned_count=0,
                total_size=0, category_totals={}, risk_totals={},
                findings_dicts=[])
    base.update(kw)
    return build_snapshot(**base)


# ── the version travels with the session ──────────────────────────

def test_a_new_snapshot_records_the_classifier_version():
    assert _snapshot()["classifier_version"] == CLASSIFIER_VERSION


def test_a_session_saved_before_versions_existed_is_outdated(qapp):
    data = _snapshot()
    data.pop("classifier_version")
    state = ScanState()
    state.restore_from_session(data)
    assert state.classifier_is_current() is False


def test_a_session_from_this_classifier_is_current(qapp):
    state = ScanState()
    state.restore_from_session(_snapshot())
    assert state.classifier_is_current() is True


def test_a_resumed_scan_reclassifies_instead_of_reusing_old_verdicts(qapp):
    data = _snapshot()
    data["classifier_version"] = CLASSIFIER_VERSION - 1
    state = ScanState()
    state.restore_from_session(data)
    state._entities = [object()]
    state._resume_baseline_count = 5
    state._findings = [object()] * 5
    assert state._can_reuse_restored_entities() is False


def test_a_resumed_scan_still_reuses_current_verdicts(qapp):
    state = ScanState()
    state.restore_from_session(_snapshot())
    state._entities = [object()]
    state._resume_baseline_count = 5
    state._findings = [object()] * 5
    assert state._can_reuse_restored_entities() is True


# ── the gate ──────────────────────────────────────────────────────

class _State:
    def __init__(self, current=True, age=0.0):
        self._current = current
        self._age = age

    def classifier_is_current(self):
        return self._current

    def results_age_seconds(self, now=None):
        return self._age


def test_outdated_results_are_refused():
    assert cleanup_gate(_State(current=False)) == OUTDATED


def test_old_results_are_confirmed():
    assert cleanup_gate(_State(age=STALE_AFTER_SECONDS + 60)) == STALE


def test_fresh_current_results_go_straight_through():
    assert cleanup_gate(_State(age=60)) == USABLE


def test_outdated_wins_over_stale():
    assert cleanup_gate(_State(current=False, age=10 * 86400)) == OUTDATED


# ── the Findings entry point obeys it ─────────────────────────────

@pytest.fixture
def dialogs(monkeypatch):
    opened, told, asked = [], [], []
    from app.screens import findings_dashboard as fd

    class _Dialog:
        def __init__(self, **kw):
            opened.append(kw)

        def exec(self):
            return 0

        def cleanup_result(self):
            return None

    monkeypatch.setattr(fd, "CleanupConfirmDialog", _Dialog)
    monkeypatch.setattr(fd.QMessageBox, "information",
                        lambda *a, **k: told.append(a))
    monkeypatch.setattr(fd.QMessageBox, "question",
                        lambda *a, **k: asked.append(a) or fd.QMessageBox.No)
    monkeypatch.setattr("app.services.bin_emptier.is_emptying", lambda: False)
    return opened, told, asked


class _Screen:
    def __init__(self, state):
        self._scan_state = state


ITEM = [{"path": "D:/Temp", "risk": "Safe", "entity_type": "temp_folder"}]


def _run(state):
    # _run_cleanup belongs to the category view that owns the selection.
    from app.screens.findings_dashboard import CategoryDetailView
    return CategoryDetailView._run_cleanup(_Screen(state), ITEM)


def test_findings_refuses_to_clean_from_an_older_classifier(qapp, dialogs):
    opened, told, asked = dialogs
    assert _run(_State(current=False)) is False
    assert opened == [], "the cleanup dialog opened on outdated verdicts"
    assert len(told) == 1


def test_findings_asks_before_cleaning_from_old_results(qapp, dialogs):
    opened, told, asked = dialogs
    assert _run(_State(age=STALE_AFTER_SECONDS + 60)) is False
    assert len(asked) == 1
    assert opened == [], "answering No still opened the dialog"


def test_findings_opens_the_dialog_for_fresh_current_results(qapp, dialogs):
    opened, told, asked = dialogs
    _run(_State(age=60))
    assert len(opened) == 1
    assert told == [] and asked == []
