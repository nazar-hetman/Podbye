"""What the History screen shows about a past run.

Reviewed alongside the other screens. The complaints were about legibility, but
two of them were carrying a factual problem underneath: a column that repeated
one constant on every row, and a metric that restated its own neighbours in a
word naming no unit.
"""
import time

import pytest
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel, QPushButton

import app.screens.history as H
from app.fonts import FONT_UI, load_fonts
from app.i18n import set_language
from app.screens.history import _cleanup_status
from app.state import session_store as ss
from app.themes import theme_manager as tm
from app.widgets.controls import ElidedLabel


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
    """Four moved and six refused is Partial: something was achieved. Only a
    run that achieved nothing gets the red word."""
    assert _cleanup_status(_rec(4, 0, 6))[0] == "Partial"
    assert _cleanup_status(_rec(0, 0, 6))[0] == "Attention"


# ── the ambiguous metric is gone from both panels ─────────────────

def _label_texts(widget):
    return [l.text() for l in widget.findChildren(QLabel)]


def _metric_keys(widget):
    """The eyebrow labels of the metrics row, in order.

    Values are excluded by requiring letters only: "954 MB" and the target
    "C:/" are both .isupper(), which is exactly what made earlier versions of
    this helper read a value as a key.
    """
    return [t for t in _label_texts(widget)
            if t.isupper() and 2 < len(t) < 16
            and all(ch.isalpha() or ch == " " for ch in t)]


def test_the_cleanup_panel_opens_on_what_was_cleaned(screen, qapp):
    """The leading slot held IMPACT, then RESULT. Both restated the row above:
    the STATUS column carries the outcome and the panel opens directly beneath
    that row, so the widest slot was spent on a word already read."""
    screen._toggle_cleanup_detail(2)
    qapp.processEvents()
    keys = _metric_keys(screen._cleanup_detail_widget)
    # RECYCLED, not CLEANED: this record's mode is recycle_bin, and moving a
    # file to the bin on the same volume frees nothing until the bin is
    # emptied. A permanent delete still says CLEANED.
    assert keys[:3] == ["RECYCLED", "ITEMS", "NOT REMOVED"]
    assert "IMPACT" not in keys and "RESULT" not in keys
    # "ATTENTION" counted protected paths Podbye had correctly refused to
    # touch, so a run that did exactly the right thing reported 170 of them.
    assert "ATTENTION" not in keys


def test_the_session_panel_opens_on_what_can_be_reclaimed(screen, qapp):
    screen._toggle_sess_detail(0)
    qapp.processEvents()
    keys = [k for k in _metric_keys(screen._sess_detail_widget) if k != "TARGET"]
    # FREED only appears once something from this session has been cleaned.
    assert [k for k in keys[:5] if k != "FREED"][:4] == [
        "RECLAIMABLE", "FOUND", "REVIEW", "DURATION"]
    assert "IMPACT" not in keys and "RESULT" not in keys


def test_the_session_panel_still_says_how_the_run_ended(screen, qapp):
    """The sessions table has no STATUS column — a completed run is not marked
    in the row at all — so dropping the metric slot must not lose the status."""
    screen._toggle_sess_detail(0)
    qapp.processEvents()
    lines = [t for t in _label_texts(screen._sess_detail_widget) if "scanned" in t]
    assert lines, "the summary line is missing"
    assert "Completed" in lines[0] or "Stopped" in lines[0]


def test_no_impact_bucket_survives_anywhere():
    """"High" named no unit, and was computed entirely from values printed
    beside it."""
    import inspect
    src = inspect.getsource(H)
    assert "_impact_label" not in src
    assert "_impact_color" not in src


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


# ── Ukrainian presentation of stored session values ───────────────

def _visible_texts(widget):
    labels = [label.text() for label in widget.findChildren(QLabel)
              if label.isVisibleTo(widget) and label.text().strip()]
    buttons = [button.text() for button in widget.findChildren(QPushButton)
               if button.isVisibleTo(widget) and button.text().strip()]
    return labels + buttons


def _fit_faults(widget):
    faults = []
    for label in widget.findChildren(QLabel):
        if (not label.isVisibleTo(widget) or isinstance(label, ElidedLabel)
                or not label.text().strip()):
            continue
        if label.wordWrap():
            if label.heightForWidth(label.width()) > label.height() + 1:
                faults.append(label.text())
        elif label.sizeHint().width() > label.width() + 1:
            faults.append(label.text())
    for button in widget.findChildren(QPushButton):
        if button.isVisibleTo(widget) and button.sizeHint().width() > button.width() + 1:
            faults.append(button.text())
    return faults


def test_ukrainian_history_localizes_known_stored_values_and_preserves_paths(qapp, monkeypatch):
    """Targets, modes, and category ids are persisted in English.

    Scope and mode are product vocabulary, so they render in the selected UI
    language. A filesystem path is user data and must remain exactly stored.
    """
    previous_font = qapp.font()
    load_fonts()
    qapp.setFont(QFont(FONT_UI, 10))
    set_language("Ukrainian")
    monkeypatch.setattr(ss, "load_cleanup_records", lambda: [_rec(8, 0, 2, state="partial")])
    monkeypatch.setattr(ss, "load_history", lambda: [{
        "session_id": "all", "start_time": time.time(), "saved_at": time.time() + 60,
        "target": "All drives", "scan_mode": "smart", "status": "stopped",
        "display_count": 1_311, "total_size": 3_708_845,
        "risk_totals": {"Review": 838, "Protected": 52, "Optional": 59},
        "category_totals": {"Images": {"count": 1311, "size_bytes": 3_708_845}},
    }, {
        "session_id": "path", "start_time": time.time(), "saved_at": time.time() + 60,
        "target": "D:/Projects/Podbye", "scan_mode": "all", "status": "completed",
        "display_count": 4, "total_size": 20, "risk_totals": {}, "category_totals": {},
    }])
    screen = H.HistoryScreen()
    screen.resize(1100, 700)
    screen.refresh()
    screen.show()
    for _ in range(8):
        qapp.processEvents()
    try:
        screen._toggle_sess_detail(0)
        for _ in range(8):
            qapp.processEvents()
        text = "\n".join(_visible_texts(screen))
        assert "Усі диски" in text and "All drives" not in text
        assert "Адаптивний аналіз" in text
        assert "Зупинено (частково)" in text
        assert "Images" in text and "Зображення" not in text
        assert "на перевірку: 838 · захищених: 52 · необов’язкових: 59" in text
        assert "Повторити аналіз" in text
        assert "сканувань:" not in text and "аналізів:" in text
        assert _fit_faults(screen._sess_detail_widget) == []

        # The table still contains an untouched actual path from the same
        # stored index; only semantic values are passed through tr().
        assert screen._sess_table.item(1, 1).toolTip() == "D:/Projects/Podbye"
    finally:
        screen.deleteLater()
        qapp.processEvents()
        set_language("English")
        qapp.setFont(previous_font)


def test_new_partly_successful_cleanup_is_stored_as_partial(tmp_path, monkeypatch):
    """A saved result must not turn a partly successful cleanup into Attention."""
    from app.services.cleanup_engine import CleanupResult

    monkeypatch.setattr(ss, "_sessions_dir", lambda: tmp_path)
    result = CleanupResult(succeeded=["C:/ok"], failed=["C:/failed"])
    assert ss.save_cleanup_record("s", [], result, "recycle_bin")
    record = ss.load_cleanup_records()[0]
    assert record["result_state"] == "partial"
    assert _cleanup_status(record)[0] == "Partial"
