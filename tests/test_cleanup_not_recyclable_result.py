"""A refusal by the Recycle Bin must not be reported as an empty category.

Reported against the installed build. C:/Users/<user>/AppData/Local/wsl is
15.4 GB, and the bin on that drive holds 9.79 GB, so cleanup_engine refused it
rather than let Windows delete it permanently — which is the protection working
exactly as intended. But the refusal was recorded in skipped_not_recyclable,
the dialog counted only skipped_protected and skipped_kept, and every counter
reaching the assessment was therefore zero. The user was told:

    Cleanup complete · No items were moved · 1 item(s) need attention
    "There was nothing removable left in Selected items."

Two statements from the same result contradicting each other, and the one in
sentence form was the opposite of the truth: the folder was not empty, it was
protected. Nothing here changes what the engine refuses.
"""
from app.services.cleanup_result_classifier import (
    assess_cleanup_counts,
    STATE_ALREADY_CLEAN,
    STATE_NOT_RECYCLABLE,
    STATE_SKIPPED,
    STATE_SUCCESS,
)


def _refused(n=1, reason="too_large", **kw):
    return assess_cleanup_counts(
        succeeded_count=0, in_use_count=0, failed_count=0, skipped_count=0,
        not_recyclable_count=n, not_recyclable_reason=reason,
        category_label="Selected items", **kw)


def test_a_refused_item_is_not_an_empty_category():
    """The reported bug, stated as the thing that must never happen again."""
    a = _refused()
    assert a.state != STATE_ALREADY_CLEAN
    assert a.state == STATE_NOT_RECYCLABLE
    assert "nothing removable left" not in a.explanation_text.lower()


def test_too_large_says_so_and_says_nothing_was_deleted():
    a = _refused(reason="too_large")
    text = a.explanation_text.lower()
    assert "too large for the recycle bin" in text
    assert "nothing was deleted" in text
    # The promise that matters: the folder is still there.
    assert "permanently" in text or "left on disk" in text


def test_a_disabled_bin_gets_its_own_reason():
    """Different cause, different instruction — the user can act on each."""
    a = _refused(reason="bin_disabled")
    text = a.explanation_text.lower()
    assert "turned off" in text
    assert "nothing was deleted" in text
    assert "too large" not in text


def test_an_unknown_reason_still_refuses_to_claim_emptiness():
    """A reason code this table has not seen yet must not fall back to
    'already clean' — the fallback has to stay on the safe side."""
    a = _refused(reason="something_new")
    assert a.state == STATE_NOT_RECYCLABLE
    assert "nothing removable left" not in a.explanation_text.lower()


def test_the_count_survives_into_the_summary():
    a = _refused(n=3)
    assert a.skipped_count == 3
    assert "3" in a.summary_value


def test_protected_and_refused_together_are_both_counted():
    """A mixed batch is still 'nothing to clean', not 'already clean'."""
    a = assess_cleanup_counts(
        succeeded_count=0, in_use_count=0, failed_count=0,
        skipped_count=2, not_recyclable_count=1,
        not_recyclable_reason="too_large")
    assert a.state == STATE_SKIPPED
    assert a.skipped_count == 3


def test_nothing_at_all_is_still_already_clean():
    """The original branch has to keep working for a genuinely empty category."""
    a = assess_cleanup_counts(succeeded_count=0, in_use_count=0,
                              failed_count=0, skipped_count=0)
    assert a.state == STATE_ALREADY_CLEAN


def test_a_refusal_beside_a_success_is_still_a_success():
    a = assess_cleanup_counts(succeeded_count=4, in_use_count=0,
                              failed_count=0, skipped_count=0,
                              not_recyclable_count=1,
                              not_recyclable_reason="too_large")
    assert a.state == STATE_SUCCESS


def test_the_dialog_passes_the_engine_s_refusals_through():
    """The wiring, not just the classifier: the count and the reason code the
    engine records must reach the assessment. This is where they were dropped.
    """
    import inspect
    from app.screens import cleanup_dialog

    src = inspect.getsource(cleanup_dialog.CleanupConfirmDialog._on_finished)
    assert "skipped_not_recyclable" in src, (
        "the dialog must read the engine's not-recyclable bucket")
    assert "not_recyclable_count=" in src
    assert "not_recyclable_reason=" in src
