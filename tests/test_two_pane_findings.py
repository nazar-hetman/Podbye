"""Findings is two panes: things on the left, parts on the right.

The single list was reported as an unfinished feature, in four parts:

* *"select all visible does not select all if there are some rolled up items"*
  — measured on a real session: it armed all 384 entities, 77 of them inside
  14 collapsed group headers, and every one of those headers went on showing
  an empty checkbox. The screen under-reported what was armed, which is the
  dangerous direction for a delete button to be wrong in.
* *"some items are rolled up, some visible in items"* — two kinds of row with
  two meanings, and only some carried a chevron.
* *"roll up looks fine but it's not giving all information"* and *"can't
  explain the entire group"*.
* *"we are grouping up items and it can be unclear for the user what exactly
  he is deleting"*.

So arming moved off the left pane entirely. A **thing** (an app, a folder, a
group of both) is a place to look and carries no checkbox; a **part** is a
concrete thing on disk and is the only unit that can be armed. Every thing
states how much of itself is armed, so nothing can be selected out of sight.
"""
import pytest

from PySide6.QtCore import QCoreApplication, QEvent

from app.screens.findings_dashboard import CategoryDetailView, PartRow, ThingRow

ROAMING = r"C:\Users\n\AppData\Roaming\Contoso"
LOCAL = r"C:\Users\n\AppData\Local\Contoso"


def _e(path, name, size=1024, risk="Safe", etype="cache_folder"):
    return {"path": path, "name": name, "size": f"{size}B", "size_bytes": size,
            "risk": risk, "entity_type": etype, "category": "Cache & Temp",
            "file_count": 1, "folder_count": 0, "reclaimable_bytes": size,
            "ai_status": "none"}


# Contoso keeps data in two places; holiday-photos is on its own.
APP_PARTS = [
    _e(ROAMING, "Contoso (Roaming)", 400),
    _e(ROAMING + r"\Cache", "Cache", 300),
    _e(LOCAL, "Contoso (Local)", 200),
]
LONE = _e(r"D:\holiday-photos", "holiday-photos", 9000)


@pytest.fixture
def view(qapp):
    v = CategoryDetailView()
    v._app_index_cache = {}          # no registry in tests — path shape only
    v.resize(1400, 800)
    yield v
    # deleteLater() only *posts* a DeferredDelete, and processEvents() outside
    # a running event loop does not deliver those.
    v.close()
    v.setParent(None)
    v.deleteLater()
    qapp.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    qapp.processEvents()


def _live(pool):
    return [r for r in pool if not r.isHidden()]


def _thing(view, name):
    return next(t for t in view._things_by_key.values() if t["name"] == name)


# ── one list, one rhythm ──────────────────────────────────────────

def test_every_left_row_is_the_same_kind_of_row(view):
    view.set_category("Cache & Temp", APP_PARTS + [LONE])
    rows = _live(view._row_pool)
    assert rows and all(isinstance(r, ThingRow) for r in rows)
    assert len(rows) == 2, "one thing for the app, one for the lone folder"


def test_a_lone_folder_is_a_thing_with_one_part(view):
    view.set_category("Cache & Temp", APP_PARTS + [LONE])
    assert len(_thing(view, "holiday-photos")["parts"]) == 1


def test_nothing_on_the_left_can_be_armed(view):
    """The left pane carries no checkbox at all — that is the redesign."""
    view.set_category("Cache & Temp", APP_PARTS + [LONE])
    for row in _live(view._row_pool):
        assert not row.findChildren(type(_live(view._part_pool)[0]._check_btn))


# ── the right pane is where deciding happens ──────────────────────

def test_opening_a_thing_lists_its_parts(view):
    view.set_category("Cache & Temp", APP_PARTS + [LONE])
    view._select_thing(_thing(view, "Contoso")["key"])
    names = [r._name_lbl.text() for r in _live(view._part_pool)]
    assert names == ["Contoso (Roaming)", "Cache", "Contoso (Local)"]


def test_a_part_row_states_its_own_reason(view):
    """A checkbox beside a bare name is not something you can decide on."""
    view.set_category("Cache & Temp", APP_PARTS + [LONE])
    view._select_thing(_thing(view, "Contoso")["key"])
    assert all(r._why_lbl.text() for r in _live(view._part_pool))


def test_the_right_pane_explains_the_whole_thing(view):
    """"roll up can't explain entire group" — the summary does."""
    view.set_category("Cache & Temp", APP_PARTS + [LONE])
    view._select_thing(_thing(view, "Contoso")["key"])
    summary = view._parts_summary_lbl.text()
    assert "3 parts" in summary and "900 B" in summary


def test_the_group_keeps_a_neutral_name(view):
    """Not "Contoso (Roaming)" — that is one half of it."""
    view.set_category("Cache & Temp", APP_PARTS + [LONE])
    assert _thing(view, "Contoso")["name"] == "Contoso"


# ── a thing can never hide what is armed inside it ────────────────

def test_arming_a_part_shows_up_on_its_thing(view):
    view.set_category("Cache & Temp", APP_PARTS + [LONE])
    thing = _thing(view, "Contoso")
    view._select_thing(thing["key"])
    view._set_checked_state(thing["rows"][0], True)

    row = next(r for r in _live(view._row_pool) if r.key() == thing["key"])
    assert "1" in row._armed_lbl.text() and "3" in row._armed_lbl.text()


def test_select_all_states_itself_on_every_thing(view):
    """The reported bug: 77 items armed behind headers showing empty boxes."""
    view.set_category("Cache & Temp", APP_PARTS + [LONE])
    view._select_all_visible()

    armed = len(view._model.checked_entities())
    stated = sum(t["armed"] for t in view._things_by_key.values())
    assert armed == 4
    assert stated == armed, "the left pane does not account for what is armed"
    for row in _live(view._row_pool):
        assert row._armed_lbl.text(), f"{row._name_lbl.text()} says nothing"


def test_select_all_parts_is_scoped_to_the_thing_on_screen(view):
    view.set_category("Cache & Temp", APP_PARTS + [LONE])
    view._select_thing(_thing(view, "Contoso")["key"])
    view._select_all_parts()

    armed = {e["path"] for e in view._model.checked_entities()}
    assert armed == {ROAMING, ROAMING + r"\Cache", LOCAL}
    assert LONE["path"] not in armed


def test_the_button_says_how_many_it_will_arm(view):
    view.set_category("Cache & Temp", APP_PARTS + [LONE])
    view._btn_select_all.setVisible(True)
    view._refresh_select_all_label()
    assert "4" in view._btn_select_all.text()


# ── switching things keeps the panes in step ──────────────────────

def test_choosing_a_thing_opens_a_part_in_the_inspector(view):
    view.set_category("Cache & Temp", APP_PARTS + [LONE])
    view._select_thing(_thing(view, "Contoso")["key"])
    panel = view._right_sidebar.detail_widget
    assert panel._current_entity.get("path") in {p["path"] for p in APP_PARTS}


def test_parts_are_rebound_not_leaked_when_things_change(view):
    """The pool is reused; a smaller thing must hide the surplus rows."""
    view.set_category("Cache & Temp", APP_PARTS + [LONE])
    view._select_thing(_thing(view, "Contoso")["key"])
    assert len(_live(view._part_pool)) == 3
    view._select_thing(_thing(view, "holiday-photos")["key"])
    assert len(_live(view._part_pool)) == 1
    assert all(isinstance(r, PartRow) for r in view._part_pool)


# ── Keep, on the screen it is used from ───────────────────────

@pytest.fixture
def kept(tmp_path, monkeypatch):
    """A keep list backed by a throwaway config."""
    from app.services import keep_list
    monkeypatch.setenv("APPDATA", str(tmp_path))
    keep_list.reset_for_tests()
    yield keep_list
    keep_list.reset_for_tests()


def test_a_kept_part_cannot_be_armed(view, kept):
    kept.keep(LOCAL)
    view.set_category("Cache & Temp", APP_PARTS + [LONE])
    view._select_thing(_thing(view, "Contoso")["key"])

    row = next(r for r in _live(view._part_pool)
               if r.entity()["path"] == LOCAL)
    assert row._check_btn.isEnabled() is False
    assert row._risk_badge.text() == "KEEP"


def test_a_kept_part_says_which_folder_the_mark_is_on(view, kept):
    kept.keep(ROAMING)
    view.set_category("Cache & Temp", APP_PARTS + [LONE])
    view._select_thing(_thing(view, "Contoso")["key"])

    row = next(r for r in _live(view._part_pool)
               if r.entity()["path"] == ROAMING + r"\Cache")
    assert "contoso" in row._why_lbl.text().lower()


def test_select_all_skips_what_is_kept(view, kept):
    """The whole point: "if I click select all, it won't be selected"."""
    kept.keep(LOCAL)
    view.set_category("Cache & Temp", APP_PARTS + [LONE])
    view._select_all_visible()

    armed = {e["path"] for e in view._model.checked_entities()}
    assert LOCAL not in armed
    assert LONE["path"] in armed


def test_select_all_parts_skips_what_is_kept(view, kept):
    kept.keep(LOCAL)
    view.set_category("Cache & Temp", APP_PARTS + [LONE])
    view._select_thing(_thing(view, "Contoso")["key"])
    view._select_all_parts()

    armed = {e["path"] for e in view._model.checked_entities()}
    assert armed == {ROAMING, ROAMING + r"\Cache"}


def test_the_thing_says_how_much_of_it_is_kept(view, kept):
    kept.keep(LOCAL)
    view.set_category("Cache & Temp", APP_PARTS + [LONE])
    assert _thing(view, "Contoso")["kept"] == 1
    view._select_thing(_thing(view, "Contoso")["key"])
    assert "kept" in view._parts_summary_lbl.text()


def test_marking_keep_from_the_inspector(view, kept):
    view.set_category("Cache & Temp", APP_PARTS + [LONE])
    view._select_thing(_thing(view, "holiday-photos")["key"])
    view._toggle_keep(LONE["path"])

    assert kept.is_kept(LONE["path"]) is True
    row = next(r for r in _live(view._part_pool)
               if r.entity()["path"] == LONE["path"])
    assert row._check_btn.isEnabled() is False


def test_keeping_something_already_armed_disarms_it(view, kept):
    """A mark is not advice; it takes effect on what is already ticked."""
    view.set_category("Cache & Temp", APP_PARTS + [LONE])
    view._select_all_visible()
    assert LONE["path"] in {e["path"] for e in view._model.checked_entities()}

    view._toggle_keep(LONE["path"])
    assert LONE["path"] not in {e["path"]
                                for e in view._model.checked_entities()}


def test_the_mark_can_be_taken_back_from_the_same_button(view, kept):
    view.set_category("Cache & Temp", APP_PARTS + [LONE])
    view._toggle_keep(LONE["path"])
    view._toggle_keep(LONE["path"])
    assert kept.is_kept(LONE["path"]) is False


def test_the_inspector_agrees_with_the_row(view, kept):
    """Two badges disagreeing about one path is what makes a screen untrusted."""
    kept.keep(LONE["path"])
    view.set_category("Cache & Temp", APP_PARTS + [LONE])
    view._select_thing(_thing(view, "holiday-photos")["key"])

    row = next(r for r in _live(view._part_pool)
               if r.entity()["path"] == LONE["path"])
    panel = view._right_sidebar.detail_widget
    assert row._risk_badge.text() == "KEEP"
    assert panel._risk_badge.text() == "KEEP"


def test_a_kept_item_is_offered_no_delete_button(view, kept):
    kept.keep(LONE["path"])
    view.set_category("Cache & Temp", APP_PARTS + [LONE])
    view._select_thing(_thing(view, "holiday-photos")["key"])

    panel = view._right_sidebar.detail_widget
    assert not panel._btn_recycle.isVisible()
    assert panel._btn_keep.text() == "Stop keeping"
