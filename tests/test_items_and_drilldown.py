"""What lives inside a finding, and how you get in and out of it.

Two different questions wear the same word. "What is inside Adobe's cache?"
means *parts of one thing* — media, previews, logs — and deleting the cache
deletes all of them together. "What is inside E:\\Work\\Projects?" means
*other things* — Focus, Vigil, Forge — each of which is a separate decision a
person might make on a separate day. The first is CONTENTS. The second is
ITEMS, and these tests hold the line between them.
"""
import os

import pytest

from PySide6.QtCore import QCoreApplication, QEvent

from app.models.entity_contents import (
    MODE_ITEMS, child_entities, items_summary,
)


def entity(path, name=None, size=0, files=1, **extra):
    out = {"path": path, "name": name or os.path.basename(path.rstrip("/\\")),
           "size_bytes": size, "file_count": files}
    out.update(extra)
    return out


WORKSPACE = entity("E:/Work/Projects", size=261_000_000_000)
FOCUS = entity("E:/Work/Projects/Focus", size=250_000_000_000)
ANSOMATIC = entity("E:/Work/Projects/Ansomatic", size=5_000_000_000)
NODE_MODULES = entity("E:/Work/Projects/Focus/app/node_modules",
                      name="node_modules", size=3_000_000_000)
DEEP_DB = entity("E:/Work/Projects/Focus/run/a/b/c/d/database.db",
                 size=1_000_000_000)
ELSEWHERE = entity("E:/Other", size=99)

WORLD = [WORKSPACE, FOCUS, ANSOMATIC, NODE_MODULES, DEEP_DB, ELSEWHERE]


# ── which children are mine ───────────────────────────────────────

def test_items_are_the_things_that_live_inside():
    names = [e["name"] for e in child_entities(WORKSPACE, WORLD)]
    assert names == ["Focus", "Ansomatic"]


def test_a_grandchild_belongs_to_its_own_parent():
    """node_modules is Focus's problem, not the workspace's.

    Listing it under Projects would say the workspace directly contains a
    node_modules folder, which is the kind of half-truth that makes a person
    stop trusting the rest of the screen.
    """
    assert NODE_MODULES not in child_entities(WORKSPACE, WORLD)
    assert NODE_MODULES in child_entities(FOCUS, WORLD)


def test_nothing_buried_deep_is_called_an_item():
    """A database six levels down is not an item of anything above it."""
    assert DEEP_DB not in child_entities(FOCUS, WORLD)
    assert DEEP_DB not in child_entities(WORKSPACE, WORLD)


def test_a_sibling_is_not_an_item():
    assert ELSEWHERE not in child_entities(WORKSPACE, WORLD)


def test_a_thing_is_not_inside_itself():
    assert WORKSPACE not in child_entities(WORKSPACE, WORLD)


def test_windows_separators_and_case_do_not_hide_a_child():
    parent = entity("E:\\Work\\Projects")
    assert [e["name"] for e in child_entities(parent, WORLD)] == \
        ["Focus", "Ansomatic"]


def test_two_buckets_in_one_folder_are_not_each_others_items():
    """A loose-file bucket's path is where its files sit, not a subtree.

    Downloads produces several buckets that all carry the Downloads path.
    Neither owns the other, and neither owns the folder.
    """
    videos = entity("C:/Users/N/Downloads", name="Videos", size=9,
                    removable_file_paths=["C:/Users/N/Downloads/a.mp4"])
    archives = entity("C:/Users/N/Downloads", name="Archives", size=8,
                      removable_file_paths=["C:/Users/N/Downloads/b.zip"])
    assert child_entities(videos, [videos, archives]) == []


def test_a_bucket_does_not_swallow_a_real_entity_below_it():
    bucket = entity("E:/Work", name="Videos", size=9,
                    removable_file_paths=["E:/Work/a.mp4"])
    world = [bucket, entity("E:/Work/Renders", size=5_000_000_000)]
    assert [e["name"] for e in child_entities(bucket, world)] == ["Renders"]


# ── the section it produces ───────────────────────────────────────

def test_items_carry_their_own_total():
    """The header's size cannot stand in for the items' size.

    A parent's displayed size has its surviving children subtracted from it
    by the disjointness pass — measured on a real scan, ``_src`` shows 2.4 GB
    while holding 30 GB of projects. Reusing that number here would be wrong
    in both directions at once.
    """
    summary = items_summary(WORKSPACE, WORLD)
    assert summary.mode == MODE_ITEMS
    assert summary.total_bytes == FOCUS["size_bytes"] + ANSOMATIC["size_bytes"]
    assert summary.total_bytes != WORKSPACE["size_bytes"]


def test_items_are_biggest_first():
    rows = items_summary(WORKSPACE, WORLD).rows
    assert [r.size_bytes for r in rows] == sorted(
        [r.size_bytes for r in rows], reverse=True)


def test_items_keep_the_path_so_it_can_be_opened():
    rows = items_summary(WORKSPACE, WORLD).rows
    assert all(r.path for r in rows)
    assert rows[0].path == FOCUS["path"]


def test_nothing_inside_means_no_items_section():
    assert not items_summary(ANSOMATIC, WORLD)


def test_the_consequence_line_does_not_repeat_the_item_list():
    """The table already said what is inside. Saying it again in prose is
    the duplication the inspector rewrite set out to remove."""
    from app.models.entity_contents import removal_consequence
    summary = items_summary(WORKSPACE, WORLD)
    assert removal_consequence(WORKSPACE, summary) == ""


# ── the inspector, driven for real ────────────────────────────────

def _ui(e, **extra):
    """The same entity, with the fields the dashboard reads off a row."""
    out = dict(e)
    out.update({"size": f"{e['size_bytes']}B", "risk": "Review",
                "entity_type": "dev_project", "category": "Dev Artifacts",
                "folder_count": 0, "reclaimable_bytes": e["size_bytes"],
                "ai_status": "none"})
    out.update(extra)
    return out


@pytest.fixture
def view(qapp):
    from app.screens.findings_dashboard import CategoryDetailView
    v = CategoryDetailView()
    v._app_index_cache = {}
    v.resize(1400, 900)
    v.set_category("Dev Artifacts",
                   [_ui(e) for e in (WORKSPACE, FOCUS, ANSOMATIC,
                                     NODE_MODULES)])
    yield v
    v.stop_background_work()
    v.close()
    v.setParent(None)
    v.deleteLater()
    qapp.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    qapp.processEvents()


def _panel(view):
    return view._right_sidebar.detail_widget


def _rows(panel):
    return [w for w in panel._content_row_pool if not w.isHidden()]


def test_the_inspector_lists_items_not_a_file_breakdown(view):
    view.select_by_path(WORKSPACE["path"])
    panel = _panel(view)
    assert panel._contents.mode == MODE_ITEMS
    assert panel._contents_title.text() == "ITEMS"
    assert [w._name.text() for w in _rows(panel)] == ["Focus", "Ansomatic"]


def test_an_item_row_offers_no_checkbox(view):
    """Focus already has a row of its own, with a box of its own. A second
    box for the same thing is two controls that can disagree."""
    view.select_by_path(WORKSPACE["path"])
    for widget in _rows(_panel(view)):
        assert widget._check.isHidden()
        assert widget._drillable


def test_clicking_an_item_inspects_it(view):
    view.select_by_path(WORKSPACE["path"])
    _rows(_panel(view))[0].clicked.emit(FOCUS["path"])
    panel = _panel(view)
    assert panel._current_entity["path"] == FOCUS["path"]
    assert view._inspected_path == FOCUS["path"]


def test_drilling_in_shows_the_way_back(view):
    view.select_by_path(WORKSPACE["path"])
    panel = _panel(view)
    assert panel._crumb_btn.isHidden()
    view._drill_into(FOCUS["path"])
    assert not panel._crumb_btn.isHidden()
    assert "Projects" in panel._crumb_btn.text()


def test_the_way_back_goes_back(view):
    view.select_by_path(WORKSPACE["path"])
    view._drill_into(FOCUS["path"])
    _panel(view)._crumb_btn.click()
    assert _panel(view)._current_entity["path"] == WORKSPACE["path"]
    assert _panel(view)._crumb_btn.isHidden()


def test_the_trail_grows_and_shrinks_one_level_at_a_time(view):
    view.select_by_path(WORKSPACE["path"])
    view._drill_into(FOCUS["path"])
    view._drill_into(NODE_MODULES["path"])
    assert view._trail_names() == ["Projects", "Focus"]
    view._drill_back()
    assert view._inspected_path == FOCUS["path"]
    view._drill_back()
    assert view._inspected_path == WORKSPACE["path"]
    assert view._inspect_trail == []


def test_selecting_something_else_leaves_the_drill(view):
    view.select_by_path(WORKSPACE["path"])
    view._drill_into(FOCUS["path"])
    view.select_by_path(ANSOMATIC["path"])
    assert view._inspect_trail == []
    assert _panel(view)._crumb_btn.isHidden()


def test_drilling_only_ever_reaches_a_finding(view):
    """Not a file browser. A path that is not an entity does nothing."""
    view.select_by_path(WORKSPACE["path"])
    view._drill_into("E:/Work/Projects/Focus/app/main.py")
    assert view._inspected_path == WORKSPACE["path"]
    assert view._inspect_trail == []


def test_the_tick_follows_the_inspector_down(view):
    """Armed inside Focus means Focus is armed, not the workspace."""
    view.select_by_path(WORKSPACE["path"])
    view._drill_into(FOCUS["path"])
    panel = _panel(view)
    view._arm_path(FOCUS["path"], True)
    view._sync_inspector_arm()
    assert panel._check_btn.isChecked()
    armed = {e["path"] for e in view._model.checked_entities()}
    assert armed == {FOCUS["path"]}


def test_a_thing_with_no_items_still_explains_itself(view):
    """Ansomatic holds no entities, so it falls back to CONTENTS/FILES
    rather than showing an empty ITEMS heading."""
    view.select_by_path(ANSOMATIC["path"])
    panel = _panel(view)
    assert panel._contents is None or panel._contents.mode != MODE_ITEMS


def test_drilling_into_something_empty_does_not_leave_the_last_list_behind(view):
    """Found by driving the real screen, not by a unit test.

    Qt's isHidden() is a widget's own flag, not its ancestors'. Rows left
    bound inside a section that has been hidden report themselves as visible,
    and come back showing the previous entity's contents. Drilling from a
    folder with items into one without is exactly that sequence.
    """
    view.select_by_path(WORKSPACE["path"])
    assert _rows(_panel(view)), "the workspace has items to leave behind"

    view._drill_into(ANSOMATIC["path"])
    panel = _panel(view)
    assert panel._contents_title.text() == ""
    assert panel._contents_meta.text() == ""
    assert not _rows(panel)
