"""Cleanup result messaging when only protected items were skipped."""
from app.services.cleanup_result_classifier import (
    assess_cleanup_counts, STATE_SKIPPED, STATE_SUCCESS,
)


def test_skipped_only_is_not_reported_as_success():
    """0 moved + only protected skipped must NOT say 'cleaned successfully'.

    The screenshot bug: "No items were moved · 1 protected item(s) skipped"
    followed by "cleaned successfully · all files moved safely".
    """
    a = assess_cleanup_counts(succeeded_count=0, in_use_count=0,
                              failed_count=0, skipped_count=1)
    assert a.state == STATE_SKIPPED
    assert a.state != STATE_SUCCESS
    lower = a.explanation_text.lower()
    assert "nothing was removed" in lower
    assert "moved safely" not in lower
    assert "cleaned successfully" not in lower


def test_skipped_only_explains_protection():
    a = assess_cleanup_counts(succeeded_count=0, in_use_count=0,
                              failed_count=0, skipped_count=3,
                              category_label="Selected items")
    assert "protected" in a.explanation_text.lower()
    assert "3" in a.summary_value


def test_real_success_still_says_success():
    a = assess_cleanup_counts(succeeded_count=5, in_use_count=0,
                              failed_count=0, skipped_count=0)
    assert a.state == STATE_SUCCESS


def test_moved_with_protected_skips_still_success():
    """Some moved + some protected skipped is a genuine success, not skipped-only."""
    a = assess_cleanup_counts(succeeded_count=5, in_use_count=0,
                              failed_count=0, skipped_count=2)
    assert a.state == STATE_SUCCESS


def test_all_zero_is_already_clean_not_skipped():
    a = assess_cleanup_counts(succeeded_count=0, in_use_count=0,
                              failed_count=0, skipped_count=0)
    assert a.state != STATE_SKIPPED
