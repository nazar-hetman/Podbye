"""The inspector panel scrolls. Nothing inside it scrolls too.

Reported: viewing an item with a default explanation, then asking the AI, put
two vertical scrollbars side by side at the right edge of the panel.

The AI answer went into a 132 px QTextEdit that scrolled itself, wrapped in a
156 px QScrollArea, sitting inside the sidebar's own page-level scroll area.
Once the window was short enough for the page to need its bar, a long answer
produced one bar for the answer box and one for the page. It is height
dependent, which is why it does not show on a tall window: measured on a
500 px-wide sidebar, two bars appear at 560 px tall and fewer, one at 700.

Two properties are pinned here — how many scrollbars a state produces, and
that the label and the text edit are never on screen together.
"""
import os
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


LONG_ANSWER = ("This application keeps a background helper running at sign-in "
               "so its features stay available across other apps. ") * 8

ENTITY = {
    "path": "C:/Program Files/Microvirt", "name": "MEmu (Microvirt)",
    "risk": "Review", "entity_type": "installed_application",
    "category": "Applications", "size": "40.2 GB", "size_bytes": 43_000_000_000,
    "file_count": 479, "folder_count": 57,
}


def _sidebar(qapp, height):
    from app.screens.findings_dashboard import RightSidebar
    side = RightSidebar(open_cb=lambda p: None, copy_cb=lambda p: None,
                        # Takes the regenerate flag: the same button is
                        # "Ask AI" with no answer and "Ask again" with one.
                        ask_ai_cb=lambda e, regenerate=False: "")
    side.resize(500, height)
    side.show()
    return side


def _visible_vbars(widget, qapp):
    from PySide6.QtWidgets import QScrollBar
    from PySide6.QtCore import Qt
    qapp.processEvents()
    return [b for b in widget.findChildren(QScrollBar)
            if b.orientation() == Qt.Vertical and b.isVisible()]


# ── the reported symptom ──────────────────────────────────────────

@pytest.mark.parametrize("height", [700, 560, 460, 400])
def test_an_ai_answer_never_adds_a_second_scrollbar(qapp, height):
    """At every window height, the panel is the only thing that scrolls."""
    side = _sidebar(qapp, height)
    side.populate({**ENTITY, "ai_status": "ready", "ai_explanation": LONG_ANSWER})

    bars = _visible_vbars(side, qapp)

    assert len(bars) <= 1, (
        f"{len(bars)} scrollbars at {height}px — the answer box is scrolling "
        f"inside a panel that already scrolls")


def test_asking_ai_from_a_default_explanation_keeps_one_scrollbar(qapp):
    """The reported sequence, at a height where the page needs its own bar."""
    side = _sidebar(qapp, 560)
    side.populate({**ENTITY, "ai_status": "none"})
    side.populate({**ENTITY, "ai_status": "ready", "ai_explanation": LONG_ANSWER})

    assert len(_visible_vbars(side, qapp)) <= 1


DUPLICATE = {
    **ENTITY, "entity_type": "duplicate_group", "dup_reclaimable": 1 << 28,
    "duplicate_locations": [
        {"path": f"C:/copies/folder_{i}/asset_{i}.bin", "size": "12 MB",
         "modified": "2026-01-02", "role": "review"} for i in range(24)],
}

LOOSE = {
    **ENTITY, "entity_type": "archive_group", "actionability": "recycle",
    "removable_file_paths": [f"C:/dl/archive_{i}.zip" for i in range(60)],
}


@pytest.mark.parametrize("height", [700, 560, 460])
@pytest.mark.parametrize("state", [
    pytest.param({**ENTITY, "ai_status": "ready", "ai_explanation": LONG_ANSWER},
                 id="ai-answer"),
    pytest.param(DUPLICATE, id="duplicate-copies"),
    pytest.param(LOOSE, id="loose-files"),
    pytest.param({**DUPLICATE, "ai_status": "ready",
                  "ai_explanation": LONG_ANSWER}, id="duplicate-plus-answer"),
])
def test_no_inspector_state_stacks_two_scrollbars(qapp, state, height):
    """The reported case was the AI answer; the copies list did the same thing.

    Both were fixed-height QTextEdits with their own scrollbar inside the
    panel's scroll area, so this sweeps the states rather than the one bug.
    """
    side = _sidebar(qapp, height)
    side.populate(state)

    bars = _visible_vbars(side, qapp)

    assert len(bars) <= 1, f"{len(bars)} scrollbars at {height}px"


def test_a_long_answer_is_fully_readable_by_scrolling_the_panel(qapp):
    """Removing the inner scrollbar must not mean the text is cut off."""
    side = _sidebar(qapp, 560)
    side.populate({**ENTITY, "ai_status": "ready", "ai_explanation": LONG_ANSWER})
    qapp.processEvents()

    text = side.detail_widget._ai_text
    doc_height = text.document().size().height()

    assert text.height() >= doc_height - 1, (
        "the answer box is shorter than its text and can no longer scroll")


def _panel(qapp, ask_ai=None):
    from app.screens.findings_dashboard import _PreallocDetailPanel
    return _PreallocDetailPanel(
        open_cb=lambda p: None,
        copy_cb=lambda p: None,
        ask_ai_cb=ask_ai,
    )


WITH_PROSE = {
    "path": "C:/Apps/Thing", "name": "Thing", "risk": "Review",
    "entity_type": "installed_application", "size": "2.0 GB",
    "ai_status": "ready",
    "ai_explanation": "A long AI answer. " * 40,
}

NO_PROSE = {
    "path": "C:/Apps/Other", "name": "Other", "risk": "Review",
    "entity_type": "installed_application", "size": "1.0 GB",
    "ai_status": "none",
}


def _visible_reasoning_widgets(panel):
    return [name for name, w in (("label", panel._ai_content_lbl),
                                 ("text", panel._ai_text)) if w.isVisible()]


def test_only_one_reasoning_widget_shows_for_an_ai_answer(qapp):
    panel = _panel(qapp)
    panel.populate(WITH_PROSE)
    panel.show()

    assert _visible_reasoning_widgets(panel) == ["text"]


def test_only_one_reasoning_widget_shows_for_a_default_explanation(qapp):
    panel = _panel(qapp, ask_ai=lambda e, regenerate=False: "")
    panel.populate(NO_PROSE)
    panel.show()

    assert _visible_reasoning_widgets(panel) == ["label"]


def test_asking_ai_after_viewing_an_answered_item_shows_one_widget(qapp):
    """The reported repro, in order: answered item, then Ask AI on another."""
    panel = _panel(qapp, ask_ai=lambda e, regenerate=False: "")
    panel.show()

    panel.populate(WITH_PROSE)          # leaves _ai_has_long_reasoning True
    panel.populate(NO_PROSE)            # default explanation, Ask AI offered
    panel._on_ask_ai_clicked()

    assert _visible_reasoning_widgets(panel) == ["label"], (
        "the previous item's answer is stacked under the analyzing note — "
        "that is the second scrollbar")


def test_the_stale_answer_is_not_left_on_screen(qapp):
    panel = _panel(qapp, ask_ai=lambda e, regenerate=False: "")
    panel.show()
    panel.populate(WITH_PROSE)
    panel.populate(NO_PROSE)
    panel._on_ask_ai_clicked()

    assert not panel._ai_text.isVisible()
    assert "analysis finishes" in panel._ai_content_lbl.text()


@pytest.mark.parametrize("entity", [
    WITH_PROSE,
    NO_PROSE,
    {**NO_PROSE, "ai_status": "failed", "ai_error": "model offline"},
    {**NO_PROSE, "ai_status": "analyzing"},
    {**NO_PROSE, "entity_type": "duplicate_group", "duplicate_locations": []},
])
def test_no_state_ever_shows_both(qapp, entity):
    """Sweep every branch that writes the block, not just the reported one."""
    panel = _panel(qapp, ask_ai=lambda e, regenerate=False: "")
    panel.show()
    panel.populate(entity)

    assert len(_visible_reasoning_widgets(panel)) <= 1, entity.get("ai_status")
