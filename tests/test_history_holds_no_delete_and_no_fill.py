"""Two things History no longer does.

**It does not offer to delete a session.** The panel carried an irreversible
"Delete from history" beside Open findings and Re-run analysis. It was worth
less than the risk of reaching for it: History keeps only the five most recent
sessions and drops the rest on its own, so the button removed what a minute of
ordinary use would have removed anyway — and it existed only on the analysis
panel, so the cleanup list beside it never had one. Automatic retention is
untouched; only the manual action and the store call written for it are gone.

**A selected row is no longer filled.** Selecting a cleanup painted the whole
row in ``accent_soft`` on top of opening the detail panel below it, and the
cell-widget half of that highlight was only resynced on a hover event — so
after picking a second row the *first* one kept its block until the pointer
happened to pass over it. Two filled rows, one open panel, and on the amber
theme a heavy brown slab across a table of numbers. The panel is the feedback;
the row keeps the table's own background.
"""
import time

import pytest
from PySide6.QtWidgets import QPushButton

import app.screens.history as H
from app.state import session_store as ss
from app.themes import theme_manager as tm


def _dispose(widget, qapp):
    """Take a widget down now, not whenever the collector gets to it.

    deleteLater() only *posts* a DeferredDelete, and processEvents() outside a
    running event loop does not deliver it — so the C++ tree survives until
    Python collects the wrapper, and PySide then destroys it from inside the
    garbage collector. That is an access violation with no traceback, landing
    on whatever unrelated test the GC happened to run under: this suite died
    in ast.parse inside a locale test, ~1500 tests away from the cause.

    tests/test_switching_language_is_not_a_stall.py documents the same failure
    from the same cause.
    """
    from PySide6.QtCore import QCoreApplication, QEvent

    widget.close()
    widget.deleteLater()
    qapp.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    qapp.processEvents()


def _cleanup(session_id="s1", freed=10 ** 9, ok=10):
    return {"timestamp": time.time(), "mode": "recycle_bin",
            "total_bytes_freed": freed,
            "items": [{"path": "C:/a", "size": 1}] * ok,
            "succeeded_count": ok, "in_use_count": 0, "failed_count": 0,
            "skipped_protected_count": 0, "session_id": session_id,
            "result_state": "success"}


def _session(session_id="s1"):
    return {"session_id": session_id, "start_time": time.time(),
            "saved_at": time.time() + 60, "target": "C:/", "scan_mode": "smart",
            "status": "completed", "display_count": 1278,
            "total_size": 10 ** 12,
            "risk_totals": {"Safe": 900, "Review": 300},
            "category_totals": {}, "reclaimable_bytes": 3 * 10 ** 10}


@pytest.fixture
def screen(qapp, monkeypatch):
    monkeypatch.setattr(ss, "load_cleanup_records",
                        lambda: [_cleanup("c1"), _cleanup("c2", 5 * 10 ** 8, 4),
                                 _cleanup("c3", 2 * 10 ** 8, 7)])
    monkeypatch.setattr(ss, "load_history",
                        lambda: [_session("s1"), _session("s2")])
    s = H.HistoryScreen()
    s.resize(1500, 950)
    s.refresh()
    s.show()
    qapp.processEvents()
    yield s
    _dispose(s, qapp)


# ── the delete action is gone, not merely hidden ──────────────────

def test_the_session_panel_offers_no_delete(screen, qapp):
    screen._toggle_sess_detail(0)
    qapp.processEvents()

    texts = [b.text() for b in screen._sess_detail_widget.findChildren(QPushButton)]
    assert texts == ["Open findings", "Re-run analysis"]


def test_the_actions_people_come_for_still_work(screen, qapp):
    """Removing a neighbour must not disturb the two that carry the panel."""
    screen._toggle_sess_detail(0)
    qapp.processEvents()
    asked = []
    screen.rerun_requested.connect(lambda target: asked.append(target))

    buttons = {b.text(): b for b in screen._sess_detail_widget.findChildren(QPushButton)}
    buttons["Re-run analysis"].click()
    qapp.processEvents()

    assert asked == ["C:/"]


def test_no_delete_path_survives_anywhere():
    """The handler and the store call existed only for this button."""
    assert not hasattr(H.HistoryScreen, "_delete_session")
    assert not hasattr(ss, "delete_session_from_history")


def test_the_panel_no_longer_takes_a_delete_callback():
    """A leftover parameter is how a removed feature comes back by accident."""
    import inspect

    params = inspect.signature(H.SessionDetail.__init__).parameters
    assert "on_delete" not in params
    assert {"on_open", "on_rerun"} <= set(params)


def test_the_strings_are_gone_from_every_locale():
    """Dead keys outlive the code that used them and get retranslated forever."""
    import json
    import pathlib

    locales = pathlib.Path(H.__file__).resolve().parent.parent / "locales"
    stale = ["Delete from history", "Delete session",
             "Remove this session from history? This cannot be undone."]
    offenders = []
    for path in sorted(locales.glob("*.json")):
        table = json.loads(path.read_text(encoding="utf-8"))
        offenders += [f"{path.name}: {k}" for k in stale if k in table]
    assert offenders == [], offenders


# ── automatic retention is untouched ──────────────────────────────

def test_history_still_prunes_itself():
    """The reason a manual delete was worth so little. If either cap ever
    goes, removing the button becomes a real loss and the decision needs
    revisiting — so this asserts the retention, not merely that it is a
    number.
    """
    assert ss.MAX_ANALYZE_HISTORY == 10
    assert ss.MAX_CLEANUP_HISTORY == 10


def test_the_list_still_says_what_it_is_not_showing(screen):
    """Retention is only acceptable silently if the screen admits to it. Six
    records against a five-row list, so a note is due."""
    note = screen._limited_history_note(6, "cleanups")

    assert note is not None
    assert "older hidden" in note.text()


# ── and a selected row keeps its background ───────────────────────

def test_the_table_paints_no_selected_fill(screen):
    qss = screen._table_qss()

    assert "selection-background-color: transparent" in qss
    assert "::item:selected" not in qss


def test_selecting_a_row_still_opens_its_detail(screen, qapp):
    """The whole point of the row being selectable."""
    screen._toggle_cleanup_detail(1)
    qapp.processEvents()

    assert screen._cleanup_detail_widget is not None
    assert screen._cleanup_expanded_row == 1


def _fills(table, colour):
    return [(r, c) for r in range(table.rowCount())
            for c in range(table.columnCount())
            if (w := table.cellWidget(r, c)) is not None
            and colour in w.styleSheet()]


def test_a_second_selection_leaves_no_block_on_the_first(screen, qapp):
    """The reported shape, driven the way a pointer drives it.

    Selecting alone never painted a cell widget — ``_sync_widget_bgs`` runs
    only from the hover filter — so a test that merely calls
    ``_toggle_cleanup_detail`` twice passes with the bug fully present. It has
    to move across the rows: hover row 0, select it, move to row 1 (which
    repaints row 0 as *selected*, the block), select row 1, and leave. Row 0
    is deselected by then and nothing ever resyncs it.
    """
    accent = tm.get_palette().get("accent_soft", "#1b2e22")
    table = screen._cleanup_table
    hover = table._row_hover_filter

    hover._sync_widget_bgs(0, True)          # pointer enters row 0
    screen._toggle_cleanup_detail(0)         # click
    qapp.processEvents()
    hover._sync_widget_bgs(0, False)         # pointer leaves row 0...
    hover._sync_widget_bgs(1, True)          # ...and enters row 1
    screen._toggle_cleanup_detail(1)         # click
    qapp.processEvents()
    hover._sync_widget_bgs(1, False)         # pointer leaves the table

    assert _fills(table, accent) == [], "a selected-row fill survived"


def test_that_walk_would_have_caught_the_old_behaviour(screen, qapp):
    """Guard on the guard: the same walk, with the old selected-aware branch
    restored, must produce the block the test above forbids. Without this the
    check could silently become an assertion about nothing.
    """
    accent = tm.get_palette().get("accent_soft", "#1b2e22")
    table = screen._cleanup_table
    hover = table._row_hover_filter

    def old_sync(row, hovered):
        if row < 0:
            return
        selected = table.selectionModel().isRowSelected(row, table.rootIndex())
        bg = accent if selected else (hover._color.name() if hovered else "transparent")
        for col in range(table.columnCount()):
            w = table.cellWidget(row, col)
            if w is not None:
                w.setStyleSheet(f"background: {bg};")

    old_sync(0, True)
    screen._toggle_cleanup_detail(0)
    qapp.processEvents()
    old_sync(0, False)
    old_sync(1, True)
    screen._toggle_cleanup_detail(1)
    qapp.processEvents()
    old_sync(1, False)

    assert _fills(table, accent), "the walk cannot reproduce the reported bug"


def test_hover_highlighting_still_works(screen, qapp):
    """Removing the selected fill must not take the hover cue with it — it is
    what tells the user a row is a target at all."""
    import inspect

    source = inspect.getsource(H._RowHoverFilter._sync_widget_bgs)
    assert "hovered" in source
    assert "self._color.name()" in source


def test_the_hover_filter_no_longer_consults_the_selection(screen):
    """It kept a second copy of the highlight in step with the first. With no
    selected fill there is nothing to mirror, and mirroring it was the bug."""
    import inspect

    source = inspect.getsource(H._RowHoverFilter._sync_widget_bgs)
    assert "isRowSelected" not in source
    assert "accent_soft" not in source
