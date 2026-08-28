"""Opening a details panel must not resize the rows above it.

Reported while reviewing the screen. Measured before the fix: five category
rows sat at 72px, opening an explanation dropped every one to 52px, and
closing it again left them at 78px — taller than they started. Reading about a
category rearranged the list you were reading about, and the geometry did not
even survive a round trip.

_CategoryRow sets a minimum height and no maximum, so the rows were elastic.
With nothing at the bottom of the column to take up the slack they stretched
to fill the panel, then handed the space straight back the moment the
explanation claimed it. The fix is a trailing stretch, so surplus collects
below the panel where nothing is looking at it.
"""
import pytest

from app.screens.quick_cleanup import QuickCleanupScreen, _READY, _CLEANING, _DONE
from app.services.quick_cleanup_detector import QuickCleanupCategory
from app.services.cleanup_result_classifier import (
    CleanupAssessment, STATE_IN_USE, STATE_SUCCESS, STATE_FAILED,
)


@pytest.fixture
def screen(qapp):
    s = QuickCleanupScreen()
    s.resize(1400, 860)
    for i, (key, label) in enumerate([("temp", "Temp"), ("browser", "Browser"),
                                      ("thumbs", "Thumbs"), ("logs", "Logs"),
                                      ("crash", "Crash")]):
        s._on_category_found(QuickCleanupCategory(
            key=key, label=label, subtitle=f"C:/{key}", paths=[f"C:/{key}"],
            size_bytes=(i + 1) * 10 ** 8, file_count=(i + 1) * 100))
    s._state = _READY
    s._on_scan_done()
    s.show()
    qapp.processEvents()
    yield s
    s.deleteLater()


def _row_heights(screen, qapp):
    qapp.processEvents()
    return [r.height() for r in screen._rows]


def _assessment(text, state=STATE_IN_USE):
    return CleanupAssessment(
        state=state, succeeded_count=3, in_use_count=7, failed_count=0,
        skipped_count=0, short_label="In use", item_label="items",
        breakdown_label="7 in use", summary_key_label="in use",
        summary_value="7", explanation_text=text, actions=["Close it", "Retry"])


# ── geometry ──────────────────────────────────────────────────────

def test_opening_an_explanation_does_not_resize_the_rows(screen, qapp):
    closed = _row_heights(screen, qapp)
    screen._on_row_clicked(0)
    assert _row_heights(screen, qapp) == closed, "rows moved when the panel opened"


def test_closing_it_again_returns_to_the_same_geometry(screen, qapp):
    closed = _row_heights(screen, qapp)
    screen._on_row_clicked(0)
    screen._on_row_clicked(0)
    assert _row_heights(screen, qapp) == closed, "a round trip changed the rows"


def test_switching_between_categories_holds_the_rows_still(screen, qapp):
    screen._on_row_clicked(0)
    one = _row_heights(screen, qapp)
    screen._on_row_clicked(3)
    assert _row_heights(screen, qapp) == one


def test_the_panel_only_ever_adds_itself_below(screen, qapp):
    """The rows keep their height; the column simply gets taller."""
    closed_rows = _row_heights(screen, qapp)
    assert not screen._exp_panel.isVisible()
    screen._on_row_clicked(2)
    qapp.processEvents()
    assert screen._exp_panel.isVisible()
    assert _row_heights(screen, qapp) == closed_rows


# ── the same rule after a cleanup, including long errors ──────────

def test_a_result_explanation_does_not_resize_the_rows(screen, qapp):
    closed = _row_heights(screen, qapp)
    for row in screen._rows:
        row.show_result(_assessment("Seven files are still in use."))
    screen._state = _DONE
    screen._populate_explanation(1)
    assert _row_heights(screen, qapp) == closed


def test_a_very_long_error_scrolls_instead_of_growing_the_panel(screen, qapp):
    """The panel is fixed height for exactly this reason: an error long enough
    to need scrolling must not push the rows above it around."""
    closed = _row_heights(screen, qapp)
    long_text = ("This file is held open by a running process and cannot be "
                 "moved until that process releases it. ") * 40
    for row in screen._rows:
        row.show_result(_assessment(long_text))
    screen._state = _DONE
    screen._populate_explanation(1)
    qapp.processEvents()
    assert _row_heights(screen, qapp) == closed
    assert screen._exp_panel.height() == 204, "a long error resized the panel"
    assert len(screen._exp_text_lbl.text()) > 2000, "the text was truncated, not scrolled"


# ── reading while the cleanup runs ────────────────────────────────

def test_an_explanation_opens_while_the_cleanup_is_running(screen, qapp):
    """It used to refuse outright — which is when someone most wants to look."""
    screen._state = _CLEANING
    screen._on_row_clicked(1)
    assert screen._exp_panel.isVisible()
    assert screen._expanded_index == 1


def test_reading_during_a_run_does_not_disturb_the_rows(screen, qapp):
    closed = _row_heights(screen, qapp)
    screen._state = _CLEANING
    screen._on_row_clicked(1)
    assert _row_heights(screen, qapp) == closed


def test_a_panel_left_open_becomes_the_result_when_the_run_ends(screen, qapp):
    """Otherwise it sits there describing the world before the cleanup."""
    screen._state = _CLEANING
    screen._on_row_clicked(1)
    before = screen._exp_text_lbl.text()

    for row in screen._rows:
        row.show_result(_assessment("Seven files are still in use. Close Chrome."))
    screen._state = _DONE
    screen._populate_explanation(screen._expanded_index)

    assert screen._exp_text_lbl.text() != before, "stale explanation survived the run"
    assert "still in use" in screen._exp_text_lbl.text()


# ── what the screen calls the outcome ─────────────────────────────

def test_the_selection_count_is_not_dressed_as_a_button(screen, qapp):
    """It sits in the action row and cannot be pressed, so it must not carry
    a control's border and fill."""
    style = screen._sel_badge.styleSheet()
    assert "border: none" in style
    assert "background: transparent" in style


def test_a_clean_run_is_complete(screen, qapp):
    assert screen._completion_label(STATE_SUCCESS) == "Cleanup complete"


@pytest.mark.parametrize("state", [STATE_IN_USE, STATE_FAILED])
def test_a_run_with_items_needing_attention_is_only_finished(screen, qapp, state):
    """"Complete" directly above a list of steps still to carry out is a
    contradiction the user has to resolve themselves."""
    assert screen._completion_label(state) == "Cleanup finished"
