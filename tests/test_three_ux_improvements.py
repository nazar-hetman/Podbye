"""Startups opens working, Settings uses its width, History says what happened.

Three changes that share a theme: the app was making the user pay for something
it could have done, or read something in a column narrower than it needed, or
was calling a mostly-successful cleanup a failure.
"""
import pytest
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel

from app.fonts import FONT_UI, load_fonts
from app.services.cleanup_result_classifier import (
    STATE_FAILED, STATE_IN_USE, STATE_PARTIAL, STATE_SKIPPED, STATE_SUCCESS,
    assess_cleanup_counts,
)
from app.themes.theme_manager import build_qss


@pytest.fixture
def dressed(qapp):
    previous = qapp.font()
    load_fonts()
    qapp.setFont(QFont(FONT_UI, 10))
    qapp.setStyleSheet(build_qss("forest"))
    yield qapp
    qapp.setFont(previous)


# ── 1. Startups detects on open ───────────────────────────────────
#
# Reading the registry and the Startup folders takes 275ms on the reporting
# machine, measured over three runs of 25 entries. That is not worth an extra
# click, and the screen had nothing to show until it was paid.


def _startups(dressed, monkeypatch, entries):
    import app.screens.startups as st

    monkeypatch.setattr("app.services.startup_detector.detect_startup_entries",
                        lambda: list(entries))
    screen = st.StartupsScreen()
    screen.resize(1400, 900)
    return screen


def _entry(name="Grammarly"):
    import time
    from app.models.startup_entry import StartupEntry

    entry = StartupEntry(
        name=name, command="C:/g.exe", path="C:/g.exe", publisher="Acme",
        source="run_hkcu", source_label="User startup registry", enabled=True,
        risk="Optional", risk_reason="r", impact="Creative helper")
    entry.target_modified = time.time() - 30 * 86400
    return entry


def test_opening_the_page_starts_the_detection(dressed, monkeypatch):
    screen = _startups(dressed, monkeypatch, [_entry()])
    try:
        screen.show()
        for _ in range(10):
            dressed.processEvents()

        assert screen._entries, "the page still waits to be asked"
    finally:
        screen.deleteLater()
        dressed.processEvents()


def test_the_page_says_it_is_working_before_it_finishes(dressed, monkeypatch):
    """The state is painted first; the walk happens on the next turn of the
    event loop, or the page arrives finished with no sign it did anything."""
    screen = _startups(dressed, monkeypatch, [_entry()])
    try:
        screen.show()
        labels = [w.text() for w in screen.findChildren(QLabel)]

        assert any("Detecting" in t for t in labels), labels[:6]
        assert not any("Click Analyze" in t for t in labels)
    finally:
        screen.deleteLater()
        dressed.processEvents()


def test_the_manual_action_is_re_analyze(dressed, monkeypatch):
    screen = _startups(dressed, monkeypatch, [_entry()])
    try:
        screen.show()
        for _ in range(10):
            dressed.processEvents()

        assert screen._btn_analyze.text() == "Re-analyze"
    finally:
        screen.deleteLater()
        dressed.processEvents()


def test_detection_does_not_run_the_model_when_startup_ai_is_off(dressed, monkeypatch):
    """Opening a page may not start work that costs minutes. The setting
    decides, exactly as it did when the button was the only way in."""
    started = []
    import app.screens.startups as st
    monkeypatch.setattr(st.StartupAIWorker, "start",
                        lambda self: started.append(self))

    class _Store:
        def get(self, key, default=None):
            return False if key == "ai_startups_enabled" else default

    screen = _startups(dressed, monkeypatch, [_entry()])
    screen._settings_store = _Store()
    try:
        screen.show()
        for _ in range(10):
            dressed.processEvents()

        assert screen._entries, "detection must still happen"
        assert started == [], "the model ran without being asked"
    finally:
        screen.deleteLater()
        dressed.processEvents()


def test_a_machine_with_no_startup_entries_does_not_retry_forever(dressed, monkeypatch):
    screen = _startups(dressed, monkeypatch, [])
    try:
        screen.show()
        for _ in range(10):
            dressed.processEvents()
        screen.hide()
        screen.show()
        for _ in range(10):
            dressed.processEvents()

        assert screen._first_detection_started
    finally:
        screen.deleteLater()
        dressed.processEvents()


# ── 2. Settings uses the width it has ─────────────────────────────

def test_the_label_column_is_wider_than_it_was(dressed):
    import app.screens.settings as se

    assert se._LABEL_COL_WIDTH >= 300, "208px wrapped help text onto five lines"


def test_the_description_column_wraps_less(dressed):
    """The connection-mode description was 79px tall — five lines — beside a
    page that had 1919px and used a third of it."""
    import app.screens.settings as se

    screen = se.SettingsScreen()
    screen.resize(1919, 1030)
    screen.show()
    screen._switch_section("ai")
    for _ in range(8):
        dressed.processEvents()
    try:
        described = [lbl for lbl in screen.findChildren(QLabel)
                     if lbl.isVisibleTo(screen) and lbl.wordWrap()
                     and lbl.text().startswith("Local finds a model server")]
        assert described, "the description moved or was reworded"
        assert described[0].width() >= 300
        assert described[0].heightForWidth(described[0].width()) < 79
    finally:
        screen.deleteLater()
        dressed.processEvents()


def test_the_page_still_fits_the_smallest_window(dressed):
    """main() sets a 1100x700 minimum; a wider column must not need more."""
    import app.screens.settings as se

    screen = se.SettingsScreen()
    screen.resize(1100, 700)
    screen.show()
    screen._switch_section("ai")
    for _ in range(8):
        dressed.processEvents()
    try:
        for lbl in screen.findChildren(QLabel):
            if lbl.isVisibleTo(screen) and lbl.text() and not lbl.wordWrap():
                assert lbl.sizeHint().width() <= lbl.width() + 1, lbl.text()[:40]
    finally:
        screen.deleteLater()
        dressed.processEvents()


# ── 3. History distinguishes partial from failed ──────────────────
#
# "succeeded 900, failed 100" was STATE_FAILED — "Attention", in red, in the
# same bucket as a run that moved nothing at all. The reason differs from a
# locked-file case; the outcome does not.


def test_a_run_that_moved_most_of_it_is_partial():
    result = assess_cleanup_counts(succeeded_count=900, in_use_count=0,
                                   failed_count=100, category_label="Cache",
                                   retry_label="the cleanup")

    assert result.state == STATE_PARTIAL


def test_a_run_that_moved_nothing_still_needs_attention():
    result = assess_cleanup_counts(succeeded_count=0, in_use_count=0,
                                   failed_count=50, category_label="Cache",
                                   retry_label="the cleanup")

    assert result.state == STATE_FAILED


def test_the_unexpected_issues_are_still_named():
    """Only the verdict changed. The count and the guidance are untouched —
    an error is still an error, it is just not the whole story."""
    result = assess_cleanup_counts(succeeded_count=900, in_use_count=0,
                                   failed_count=100, category_label="Cache",
                                   retry_label="the cleanup")

    assert "100" in result.summary_value
    assert "unexpected" in result.explanation_text.lower()


@pytest.mark.parametrize("counts,expected", [
    (dict(succeeded_count=100, in_use_count=0, failed_count=0), STATE_SUCCESS),
    (dict(succeeded_count=90, in_use_count=10, failed_count=0), STATE_PARTIAL),
    (dict(succeeded_count=0, in_use_count=10, failed_count=0), STATE_IN_USE),
    (dict(succeeded_count=900, in_use_count=0, failed_count=100), STATE_PARTIAL),
    (dict(succeeded_count=0, in_use_count=0, failed_count=50), STATE_FAILED),
    (dict(succeeded_count=0, in_use_count=0, failed_count=0, skipped_count=12),
     STATE_SKIPPED),
])
def test_the_whole_ladder(counts, expected):
    result = assess_cleanup_counts(category_label="Cache",
                                   retry_label="the cleanup", **counts)

    assert result.state == expected


def test_history_shows_partial_for_a_mostly_successful_run(dressed):
    from app.screens.history import _cleanup_status

    label, colour = _cleanup_status({"succeeded_count": 900, "failed_count": 100,
                                     "in_use_count": 0,
                                     "skipped_protected_count": 0})

    assert label == "Partial"
    assert colour == "review", "red is for a run that achieved nothing"


def test_an_old_record_keeps_the_verdict_it_was_given(dressed):
    """Re-judging finished runs by today's rules would rewrite history."""
    from app.screens.history import _cleanup_status

    label, _ = _cleanup_status({"result_state": STATE_FAILED,
                                "succeeded_count": 900, "failed_count": 100})

    assert label == "Attention"


def _outcome_values(dressed, record):
    """The numbers the cleanup detail panel puts on screen."""
    from app.screens.history import CleanupRecordDetail

    panel = CleanupRecordDetail(record)
    panel.resize(900, 400)
    panel.show()
    for _ in range(4):
        dressed.processEvents()
    try:
        labels = [lbl.text() for lbl in panel.findChildren(QLabel)
                  if lbl.isVisibleTo(panel)]
        return labels
    finally:
        panel.deleteLater()
        dressed.processEvents()


def test_protected_skips_are_not_counted_as_a_problem(dressed):
    """Refusing to touch a protected path is Podbye working. Counting those
    made a run that did exactly what it should report twelve items to look at.
    """
    labels = _outcome_values(dressed, {
        "succeeded_count": 40, "in_use_count": 0, "failed_count": 0,
        "skipped_protected_count": 12, "total_bytes_freed": 10 ** 9,
        "items": [], "mode": "recycle_bin"})

    assert "NOT REMOVED" in labels, labels[:8]
    assert "12" not in labels, "protected skips are back in the count"
    assert "None" in labels


def test_what_was_meant_to_go_and_did_not_is_counted(dressed):
    labels = _outcome_values(dressed, {
        "succeeded_count": 40, "in_use_count": 3, "failed_count": 2,
        "skipped_protected_count": 12, "total_bytes_freed": 10 ** 9,
        "items": [], "mode": "recycle_bin"})

    assert "5" in labels, labels[:8]
