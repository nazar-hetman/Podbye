"""The AI card must describe the AI, not the size of the findings list.

Reported while reviewing the themes: Home showed "AI ACTIVE", "analyzing…" and
"0 / 1,278" on a machine where the AI had never been asked to do anything.

Bulk AI is off by default — ai_findings_enabled is False, because a local model
chokes on hundreds of entities — and the ordinary way to get an explanation is
the per-item "Ask AI" button. So on a stock install nothing is ever queued.

The old model took the denominator from the number of findings and inferred
"still working" from ready < total. Both halves were wrong at once: it invented
a queue of 1,278 jobs, then reported the AI as actively working through it,
permanently, because nothing would ever increment the numerator. The same flag
drove the "ANALYSIS READY" header and an "AI ANALYSIS: 0%" painted in the
failure colour.

What the counts mean now: attempted = items that actually entered the queue.
"none" and "disabled" are not pending work.
"""
import pytest

from app.screens.home import _session_ai_counts
from app.state import session_store


def _session(*statuses):
    """A completed session whose entities carry the given ai_status values."""
    return {"entities": [{"path": f"C:/x/{i}", "risk": "Safe", "ai_status": st}
                         for i, st in enumerate(statuses)]}


# ── the reported bug ──────────────────────────────────────────────

def test_a_scan_with_the_ai_switched_off_has_nothing_attempted():
    """1,278 findings, no AI: the denominator is zero, not 1,278."""
    s = _session(*(["none"] * 1278))
    explained, attempted, in_flight = _session_ai_counts(s)
    assert (explained, attempted, in_flight) == (0, 0, 0)


def test_nothing_attempted_is_never_reported_as_in_flight():
    """The claim that made the card wrong: 'analyzing…' with an idle queue."""
    _explained, attempted, in_flight = _session_ai_counts(_session(*(["none"] * 50)))
    assert in_flight == 0, "an unexplained item is not a queued item"
    assert attempted == 0


def test_items_explicitly_disabled_are_not_pending_work():
    _explained, attempted, in_flight = _session_ai_counts(_session("disabled", "disabled"))
    assert (attempted, in_flight) == (0, 0)


# ── the states that are real ──────────────────────────────────────

def test_a_running_queue_is_in_flight():
    explained, attempted, in_flight = _session_ai_counts(
        _session("ready", "analyzing", "pending", "none"))
    assert explained == 1
    assert attempted == 3, "the un-queued item must stay out of the denominator"
    assert in_flight == 2


def test_a_drained_queue_is_finished_not_running():
    explained, attempted, in_flight = _session_ai_counts(_session("ready", "done", "none"))
    assert (explained, attempted, in_flight) == (2, 2, 0)


def test_a_failed_explanation_was_still_attempted():
    """Otherwise a failure would silently leave the denominator."""
    explained, attempted, in_flight = _session_ai_counts(_session("ready", "failed", "cancelled"))
    assert explained == 1
    assert attempted == 3
    assert in_flight == 0


# ── what a saved session carries ──────────────────────────────────

def test_the_saved_summary_records_what_was_queued():
    summary = session_store._build_last_run_summary(
        {"entities": [{"ai_status": "ready"}, {"ai_status": "analyzing"},
                      {"ai_status": "none"}, {"ai_status": "none"}]})
    assert summary["ai_ready_count"] == 1
    assert summary["ai_attempted_count"] == 2
    assert summary["ai_active_count"] == 1
    assert summary["display_count"] == 4, "findings count is still reported, separately"


def test_the_old_findings_shaped_total_is_gone():
    """ai_total_count held a findings count. Keeping the name would let a
    session already on disk feed the old meaning straight back into the card."""
    summary = session_store._build_last_run_summary(
        {"entities": [{"ai_status": "none"} for _ in range(9)]})
    assert "ai_total_count" not in summary
    assert summary["ai_attempted_count"] == 0


def test_a_stale_session_is_recounted_rather_than_believed():
    """A summary written by the old code claims 1,278 queued. Recount it."""
    stale = {"ai_ready_count": 0, "ai_total_count": 1278,
             "entities": [{"ai_status": "none"} for _ in range(1278)]}
    explained, attempted, in_flight = _session_ai_counts(stale)
    assert (explained, attempted, in_flight) == (0, 0, 0)


def test_a_summary_written_by_the_new_code_is_trusted_as_is():
    fresh = {"ai_ready_count": 4, "ai_attempted_count": 10, "ai_active_count": 3}
    assert _session_ai_counts(fresh) == (4, 10, 3)
