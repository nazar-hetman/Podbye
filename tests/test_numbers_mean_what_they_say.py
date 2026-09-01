"""A trust audit: every figure beside a destructive control means one thing.

Four places where Podbye was technically correct and still misleading. Each is
a seam where the user has to answer "what will happen and how much will it
affect" without knowing anything about the internal model.

1. **A duplicate group's selection total counted the copy it keeps.** The row
   showed 4.0 GB reclaimable, the selection beside Move to Recycle Bin showed
   6.0 GB, and 4.0 GB went. ``size_bytes`` is the whole group — right for "how
   much disk do these copies occupy", wrong for "how much will this remove".

2. **History said space was freed when it was recycled.** Recycling is a move,
   and a move on the same volume frees nothing until the bin is emptied.
   ``recycle_bin.py`` says so at the top and Quick Cleanup shows it on its own
   results screen; History reported the space as already back.

3. **"entities" is Podbye's word for its own data model**, and it was on the
   category header, the list footer, the inspector title and the grouping
   progress panel.

4. **"Available" and "Summary"** were the same size, colour and place, so a
   model's prose and Podbye's own rule-based reasoning read identically. The
   badge described whether text existed rather than who wrote it.
"""
import re

import pytest

from app.models.finding import _format_size
from app.models.findings_table_model import FindingsTableModel
from app.models.smart_entity import SmartEntity

GB = 1024 ** 3


def _duplicate_group():
    """Three identical 2 GB copies: 6 GB on disk, 4 GB removable."""
    return SmartEntity(
        path="D:/a/movie.mkv", name="movie.mkv · 3 copies",
        entity_type="duplicate_group", size_bytes=6 * GB, file_count=3,
        risk="Optional", dup_reclaimable=4 * GB,
        removable_duplicate_paths=["D:/b/movie.mkv", "D:/c/movie.mkv"],
        duplicate_locations=[{"path": "D:/b/movie.mkv", "size_bytes": 2 * GB},
                             {"path": "D:/c/movie.mkv", "size_bytes": 2 * GB}])


# ── 1. the number beside the button is the number that goes ───────

def test_a_duplicate_selection_totals_what_will_be_removed():
    """It summed size_bytes — every copy, including the one kept on purpose."""
    from app.models.deletion_scope import deletion_scope_bytes

    entity = _duplicate_group().to_dict()

    assert deletion_scope_bytes(entity) == 4 * GB
    assert entity["size_bytes"] == 6 * GB, "the group still knows its real size"


def test_the_row_and_the_selection_agree():
    entity = _duplicate_group().to_dict()
    model = FindingsTableModel()
    model.set_entities([entity])
    model.set_checked_rows([0], True)

    assert entity["size"] == _format_size(model.checked_size()) == "4.0 GB"


def test_the_selection_agrees_with_what_cleanup_targets():
    """Straight through the real target builder — the code that decides what
    is handed to the shell."""
    from app.screens.cleanup_dialog import _cleanup_targets_for_item

    entity = _duplicate_group().to_dict()
    model = FindingsTableModel()
    model.set_entities([entity])
    model.set_checked_rows([0], True)

    targets = _cleanup_targets_for_item(entity)

    assert sum(t["size_bytes"] for t in targets) == model.checked_size()
    assert "D:/a/movie.mkv" not in {t["path"] for t in targets}, "removed the keeper"


def test_a_partly_cleaned_group_is_measured_not_remembered():
    """dup_reclaimable is a total from detection time. After some copies are
    gone the surviving list is the truth."""
    from app.models.deletion_scope import deletion_scope_bytes

    entity = _duplicate_group().to_dict()
    entity["removable_duplicate_paths"] = ["D:/c/movie.mkv"]

    assert deletion_scope_bytes(entity) == 2 * GB


def test_an_ordinary_entity_is_unaffected():
    from app.models.deletion_scope import deletion_scope_bytes

    plain = {"path": "C:/U/Cache", "entity_type": "system_cache", "size_bytes": 3 * GB}

    assert deletion_scope_bytes(plain) == 3 * GB


# ── 2. recycling is not freeing ───────────────────────────────────

def _cleanup_record(mode):
    return {"timestamp": 0, "mode": mode, "succeeded_count": 274,
            "in_use_count": 0, "failed_count": 0, "skipped_protected_count": 0,
            "total_bytes_freed": 170 * 1024 ** 2, "session_id": "s0", "items": []}


def _panel_text(qapp, record):
    from PySide6.QtWidgets import QLabel
    from app.screens.history import CleanupRecordDetail

    panel = CleanupRecordDetail(record)
    panel.resize(900, 400)
    panel.show()
    for _ in range(4):
        qapp.processEvents()
    try:
        return [l.text() for l in panel.findChildren(QLabel) if l.isVisibleTo(panel)]
    finally:
        panel.deleteLater()
        qapp.processEvents()


def test_a_recycled_cleanup_does_not_claim_the_space_is_back(qapp):
    """One machine had 16.7 GB in the bin while the user kept cleaning and
    wondering why the disk had not changed."""
    texts = _panel_text(qapp, _cleanup_record("recycle_bin"))
    joined = "\n".join(texts)

    assert "moved to the Recycle Bin" in joined, joined[:300]
    assert "freed" not in joined, "still claims the bytes are back"
    assert "RECYCLED" in texts
    assert "CLEANED" not in texts


def test_a_permanent_delete_may_say_freed(qapp):
    """Then it is true, and softening it would understate what happened."""
    texts = _panel_text(qapp, _cleanup_record("permanent"))
    joined = "\n".join(texts)

    assert "freed" in joined
    assert "CLEANED" in texts


def test_the_amount_itself_did_not_change(qapp):
    """Only the verb moved. The measurement is the same either way."""
    for mode in ("recycle_bin", "permanent"):
        assert any("170 MB" in t for t in _panel_text(qapp, _cleanup_record(mode)))


# ── 3. Podbye's data model is not vocabulary ──────────────────────

def test_the_findings_screen_does_not_say_entities(qapp):
    """"entity" is the name of a class. Everywhere else the UI says items."""
    import inspect

    import app.screens.findings_dashboard as fd

    # The tr() literals themselves, not the lines around them: an attribute
    # named _stat_entities is internal and fine, the string beside it is not.
    pattern = "tr" + chr(92) + "(" + chr(92) + 's*"([^"]*)"'
    literals = re.findall(pattern, inspect.getsource(fd))
    shown = [text for text in literals if "entit" in text.lower()]

    assert shown == [], shown


def test_the_category_header_counts_items(qapp):
    from app.themes.theme_manager import build_qss
    import app.screens.findings_dashboard as fd

    qapp.setStyleSheet(build_qss("forest"))
    view = fd.CategoryDetailView()
    view._app_index_cache = {}
    view.resize(1400, 900)
    view.show()
    view.set_category("Browser Data", [{
        "path": "C:/U/Chrome", "name": "Chrome Data", "entity_type": "browser_profile",
        "size_bytes": 5 * GB, "size": "5.0 GB", "file_count": 10, "risk": "Review",
        "category": "Browser Data", "actionability": "recycle",
        "children_sample": [], "ai_status": "none"}])
    for _ in range(8):
        qapp.processEvents()
    try:
        assert "items" in view._stats_lbl.text()
        assert "entities" not in view._stats_lbl.text()
    finally:
        view.deleteLater()
        qapp.processEvents()


# ── 4. who wrote this sentence ────────────────────────────────────

def _inspector(qapp, entity):
    from app.themes.theme_manager import build_qss
    import app.screens.findings_dashboard as fd

    qapp.setStyleSheet(build_qss("forest"))
    view = fd.CategoryDetailView()
    view._app_index_cache = {}
    view.resize(1500, 900)
    view.show()
    view.set_category(entity.get("category", "Browser Data"), [entity])
    for _ in range(8):
        qapp.processEvents()
    view.select_by_path(entity["path"])
    for _ in range(8):
        qapp.processEvents()
    return view


def _entity(**kw):
    base = {"path": "C:/U/Chrome", "name": "Chrome Data",
            "entity_type": "browser_profile",
            "entity_type_label": "Browser Profile/Data", "size_bytes": 5 * GB,
            "size": "5.0 GB", "file_count": 10, "folder_count": 2,
            "risk": "Review", "category": "Browser Data",
            "actionability": "recycle", "children_sample": [],
            "ai_status": "none"}
    base.update(kw)
    return base


def test_a_model_written_answer_says_so(qapp):
    view = _inspector(qapp, _entity(
        ai_status="ready", ai_explanation="This folder holds browser state." * 4,
        ai_language="English"))
    try:
        badge = view._detail_widget._ai_state_badge.text()
        assert "AI" in badge, badge
        assert "Available" not in badge
    finally:
        view.deleteLater()
        qapp.processEvents()


def test_podbyes_own_reasoning_is_attributed_to_podbye(qapp):
    """A duplicate group's explanation comes from a rule, not a model. It
    used to say "Summary", in the same place and style as the model's."""
    entity = _entity(entity_type="duplicate_group", name="movie.mkv · 3 copies",
                     path="D:/a/movie.mkv", risk="Optional",
                     dup_reclaimable=4 * GB,
                     duplicate_locations=[{"path": "D:/b/movie.mkv",
                                           "size_bytes": 2 * GB}],
                     removable_duplicate_paths=["D:/b/movie.mkv"],
                     category="Duplicates")
    view = _inspector(qapp, entity)
    try:
        badge = view._detail_widget._ai_state_badge.text()
        assert "Podbye" in badge, badge
        assert badge != "Summary"
    finally:
        view.deleteLater()
        qapp.processEvents()


def test_the_two_are_never_the_same_words(qapp):
    """The point of the change: a reader can tell them apart."""
    from app.i18n import tr

    assert tr("AI answer") != tr("Podbye's summary")
