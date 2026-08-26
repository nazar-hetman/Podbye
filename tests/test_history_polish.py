"""What the History screen shows about a past run.

Reviewed alongside the other screens. The complaints were about legibility, but
two of them were carrying a factual problem underneath: a column that repeated
one constant on every row, and a metric that restated its own neighbours in a
word naming no unit.
"""
import time

import pytest
from PySide6.QtWidgets import QLabel, QPushButton

import app.screens.history as H
from app.screens.history import _cleanup_status, _status_color
from app.state import session_store as ss
from app.themes import theme_manager as tm


def _rec(ok=10, in_use=0, failed=0, skipped=0, state=None):
    rec = {"timestamp": time.time(), "mode": "recycle_bin",
           "total_bytes_freed": 10 ** 9,
           "items": [{"path": "C:/a", "size": 1}] * (ok + in_use + failed),
           "succeeded_count": ok, "in_use_count": in_use, "failed_count": failed,
           "skipped_protected_count": skipped, "session_id": "s1"}
    if state:
        rec["result_state"] = state
    return rec


@pytest.fixture
def screen(qapp, monkeypatch):
    monkeypatch.setattr(ss, "load_cleanup_records", lambda: [
        _rec(40, 0, 0, state="success"),
        _rec(25, 5, 0, state="partial"),
        _rec(4, 0, 6, state="failed"),
    ])
    monkeypatch.setattr(ss, "load_history", lambda: [{
        "session_id": "s1", "start_time": time.time(), "saved_at": time.time() + 60,
        "target": "C:/", "scan_mode": "smart", "status": "completed",
        "display_count": 1278, "total_size": 10 ** 12,
        "risk_totals": {"Safe": 900, "Review": 300}, "category_totals": {},
        "reclaimable_bytes": 3 * 10 ** 10,
    }])
    s = H.HistoryScreen()
    s.resize(1500, 950)
    s.refresh()
    s.show()
    qapp.processEvents()
    yield s
    s.deleteLater()


# ── the note belongs after what it annotates ──────────────────────

def _section_order(area, table):
    lay = area.parent().layout()
    seq = []
    for i in range(lay.count()):
        w = lay.itemAt(i).widget()
        if w is table:
            seq.append("TABLE")
        elif w is area:
            seq.append("DETAIL")
        elif isinstance(w, QLabel) and "latest" in w.text().lower():
            seq.append("NOTE")
    return seq


def test_the_detail_panel_follows_the_row_it_belongs_to(qapp, monkeypatch):
    """The note used to wedge itself between the row and its own details.

    It only appears once the history outruns the visible rows, so this builds
    a screen with more records than the table shows.
    """
    monkeypatch.setattr(ss, "load_cleanup_records",
                        lambda: [_rec(state="success") for _ in range(12)])
    monkeypatch.setattr(ss, "load_history", lambda: [{
        "session_id": f"s{i}", "start_time": time.time(), "saved_at": time.time(),
        "target": "C:/", "scan_mode": "smart", "status": "completed",
        "display_count": 10, "total_size": 10 ** 9, "risk_totals": {},
        "category_totals": {},
    } for i in range(12)])

    s = H.HistoryScreen()
    s.resize(1500, 950)
    s.refresh()
    s.show()
    qapp.processEvents()
    try:
        for area, table in ((s._cleanup_detail_area, s._cleanup_table),
                            (s._sess_detail_area, s._sess_table)):
            order = _section_order(area, table)
            assert "NOTE" in order, "the truncation note did not render"
            assert order.index("TABLE") < order.index("DETAIL") < order.index("NOTE")
    finally:
        s.deleteLater()


# ── the column now varies ─────────────────────────────────────────

def test_the_cleanup_table_reports_status_not_mode(screen):
    headers = [screen._cleanup_table.horizontalHeaderItem(c).text()
               for c in range(screen._cleanup_table.columnCount())]
    assert "STATUS" in headers
    assert "MODE" not in headers


def test_each_outcome_gets_its_own_word(screen):
    labels = [screen._cleanup_table.cellWidget(r, 1).findChild(QLabel).text()
              for r in range(screen._cleanup_table.rowCount())]
    assert labels == ["Complete", "Partial", "Attention"]


@pytest.mark.parametrize("state,expected", [
    ("success", "Complete"), ("already_clean", "Complete"),
    ("partial", "Partial"), ("in_use", "Partial"), ("failed", "Attention"),
])
def test_status_words_map_to_states(state, expected):
    assert _cleanup_status(_rec(state=state))[0] == expected


def test_a_stored_verdict_wins_over_recomputation():
    """Re-judging an old run by today's rules would let a classifier change
    silently rewrite history."""
    rec = _rec(4, 0, 6, state="success")     # counts say failed, record says success
    assert _cleanup_status(rec)[0] == "Complete"


def test_a_record_without_a_stored_verdict_is_recomputed():
    assert _cleanup_status(_rec(4, 0, 6))[0] == "Attention"


# ── the ambiguous metric is gone from both panels ─────────────────

def _label_texts(widget):
    return [l.text() for l in widget.findChildren(QLabel)]


def test_the_cleanup_panel_states_a_result_instead_of_an_impact(screen, qapp):
    screen._toggle_cleanup_detail(2)
    qapp.processEvents()
    texts = _label_texts(screen._cleanup_detail_widget)
    assert "IMPACT" not in texts
    assert "RESULT" in texts
    assert texts[texts.index("RESULT") + 1] == "Attention"


def test_the_session_panel_states_a_result_instead_of_an_impact(screen, qapp):
    screen._toggle_sess_detail(0)
    qapp.processEvents()
    texts = _label_texts(screen._sess_detail_widget)
    assert "IMPACT" not in texts
    assert "RESULT" in texts


def test_no_impact_bucket_survives_anywhere():
    """"High" named no unit, and was computed entirely from values printed
    beside it."""
    import inspect
    src = inspect.getsource(H)
    assert "_impact_label" not in src
    assert "_impact_color" not in src


def test_a_stopped_scan_is_review_not_risk():
    """A partial result is not a failure."""
    p = tm.PALETTES["forest"]
    assert _status_color("stopped", p) == p["review"]
    assert _status_color("completed", p) == p["safe"]


# ── weight ────────────────────────────────────────────────────────

def test_delete_is_quieter_than_the_other_actions(screen, qapp):
    screen._toggle_sess_detail(0)
    qapp.processEvents()
    buttons = {b.text(): b for b in screen._sess_detail_widget.findChildren(QPushButton)}
    delete = buttons["Delete from history"]
    assert delete.objectName() != buttons["Open findings"].objectName()
    assert "border: none" in delete.styleSheet()
    assert "background: transparent" in delete.styleSheet()


def test_delete_is_still_there_and_still_connected(screen, qapp):
    """Quieter, not hidden — a destructive action people cannot find is worse."""
    screen._toggle_sess_detail(0)
    qapp.processEvents()
    texts = [b.text() for b in screen._sess_detail_widget.findChildren(QPushButton)]
    assert "Delete from history" in texts


def test_the_detail_panel_sits_below_the_table_surface(screen):
    """Both used panel_alt, so the annotation had the weight of the data."""
    for area in (screen._cleanup_detail_area, screen._sess_detail_area):
        p = tm.get_palette()
        assert p["tint_bg"] in area.styleSheet()
        assert p["panel_alt"] not in area.styleSheet()
