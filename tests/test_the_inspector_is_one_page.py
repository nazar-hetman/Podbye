"""The right side of Findings is one page, not a stack of panes.

It arrived here in steps, each from a report:

* two bordered frames a splitter apart read as "2 windows/frames too much";
* "PARTS OF Discord" over an inspector describing something else meant the
  thing you opened was never the thing being inspected;
* and with the parent promoted, its name and scale were then said twice - once
  by the pane header, once by the identity block under it.

So there is one column with one scrollbar, and it always describes exactly one
entity: identity, what it is made of, what that means, what you can do about
it. PARTS is a section of that page like CONTENTS is, and clicking a part
replaces the page with the part's own, with a crumb back.

The two lists are deliberately different weights. A part is separately armable
and carries its own risk, so its row keeps a checkbox, a badge and a size; a
contents row is evidence and carries neither. Both are lighter than a row in
the findings list on the left, which is a place to navigate to rather than a
thing to tick.

Replaces tests/test_one_inspector_not_two_windows.py and
tests/test_parts_pane_fits_its_rows.py, whose subjects - a splitter, a second
pane and its height - no longer exist.
"""
import tempfile
import time
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QScrollBar

import app.screens.findings_dashboard as fd
from app.themes.theme_manager import build_qss


def _entity(path, name, size, files=4, folders=2):
    return {"path": path, "name": name, "entity_type": "dev_artifact",
            "size_bytes": size, "size": fd._format_size(size),
            "file_count": files, "folder_count": folders, "risk": "Safe",
            "category": "Dev Artifacts",
            "entity_type_label": "Node.js Dependencies",
            "actionability": "recycle", "children_sample": []}


def _view(qapp, count, root_name="TypeScript"):
    qapp.setStyleSheet(build_qss("forest"))
    root = Path(tempfile.mkdtemp()) / root_name
    entities = []
    for i in range(count):
        d = root / f"part-{i:02d}" / "node_modules"
        d.mkdir(parents=True)
        (d / "index.js").write_bytes(b"x" * (5000 - i * 20))
        entities.append(_entity(str(d).replace("\\", "/"),
                                f"npm Packages - {i:02d}", 5000 - i * 20))
    view = fd.CategoryDetailView()
    view._app_index_cache = {str(root).replace("\\", "/").lower(): root_name}
    view.set_category("Dev Artifacts", entities)
    view.resize(1400, 900)
    view.show()
    for _ in range(20):
        qapp.processEvents()
    time.sleep(0.6)
    for _ in range(20):
        qapp.processEvents()
    return view


@pytest.fixture
def view(qapp):
    v = _view(qapp, 4)
    yield v
    v.deleteLater()
    qapp.processEvents()


def _parts(view):
    return [r for r in view._detail_widget._part_row_pool if not r.isHidden()]


def _bars(view):
    return [b for b in view._right_sidebar.findChildren(QScrollBar)
            if b.orientation() == Qt.Vertical and b.isVisible()]


# -- one page ------------------------------------------------------

def test_the_right_side_is_a_single_scroll(view):
    assert view._right_sidebar._scroll is not None
    assert len(_bars(view)) <= 1


def test_there_is_no_second_pane_and_nothing_to_drag(view):
    assert not hasattr(view, "_right_split")
    assert not hasattr(view, "_parts_panel")


def test_the_page_carries_one_card(view):
    assert "border: 1px solid" in view._right_sidebar.styleSheet()


# -- and it is about one entity ------------------------------------

def test_opening_a_thing_inspects_the_thing(view):
    """Not the biggest piece of it."""
    assert view._detail_widget._name_lbl.text() == "TypeScript"
    assert view._detail_widget._current_entity.get("is_group")


def test_the_thing_is_named_once(view):
    """The pane header used to say it, and the identity block said it again."""
    panel = view._detail_widget
    assert panel._name_lbl.text() == "TypeScript"
    assert "TypeScript" not in panel._parts_title.text()
    assert "TypeScript" not in panel._parts_meta.text()


def test_its_parts_are_a_section_of_that_page(view):
    assert view._detail_widget._parts_section.isVisibleTo(view)
    assert len(_parts(view)) == 4


def test_the_section_states_the_scale_it_stands_for(view):
    meta = view._detail_widget._parts_meta.text()
    assert "4 parts" in meta


def test_clicking_a_part_replaces_the_page(view, qapp):
    row = _parts(view)[1]
    row.clicked.emit(row.source_row())
    for _ in range(8):
        qapp.processEvents()
    panel = view._detail_widget

    assert panel._name_lbl.text() == "npm Packages - 01"
    assert panel._parts == [], "a part has no parts of its own"
    assert "TypeScript" in panel._crumb_btn.text(), "and a way back"


def test_the_crumb_returns_to_the_thing(view, qapp):
    row = _parts(view)[1]
    row.clicked.emit(row.source_row())
    for _ in range(8):
        qapp.processEvents()

    view._detail_widget._crumb_btn.click()
    for _ in range(8):
        qapp.processEvents()

    assert view._detail_widget._name_lbl.text() == "TypeScript"
    assert len(_parts(view)) == 4


# -- weights -------------------------------------------------------

def test_a_part_row_is_one_line(view):
    assert _parts(view)[0].height() <= 40


def test_a_part_keeps_what_makes_it_actionable(view):
    row = _parts(view)[0]

    assert row._check_btn.isVisibleTo(view)
    assert row._risk_badge.isVisibleTo(view)
    assert row._size_lbl.text()
    assert "files" in row._why_lbl.text()


def test_a_part_is_heavier_than_a_contents_row(view):
    """Both are lists on one page; only one of them is a decision."""
    part = _parts(view)[0]
    contents_row = fd.ContentRowWidget()

    assert not hasattr(contents_row, "_risk_badge")
    assert part._check_btn.isEnabled()


def test_the_repeated_half_of_the_subtitle_is_on_the_tooltip(view):
    tip = _parts(view)[0].toolTip()

    assert "Dependencies" in tip or "folders" in tip


def test_both_lists_are_boxed_and_the_prose_is_not(view):
    panel = view._detail_widget

    assert "1px solid" in panel._parts_section.styleSheet()
    assert "1px solid" in panel._contents_section.styleSheet()
    assert "border: none" in panel._ai_frame.styleSheet()


# -- long lists ----------------------------------------------------

def test_a_long_list_folds_its_tail(qapp):
    """57 parts between the identity and the classification would bury both."""
    view = _view(qapp, 20)
    try:
        assert len(_parts(view)) == view._detail_widget._PART_ROWS_SHOWN
        assert "more" in view._detail_widget._btn_parts_more.text()
    finally:
        view.deleteLater()
        qapp.processEvents()


def test_the_tail_can_be_unfolded(qapp):
    view = _view(qapp, 20)
    try:
        view._detail_widget._btn_parts_more.click()
        qapp.processEvents()

        assert len(_parts(view)) == 20
    finally:
        view.deleteLater()
        qapp.processEvents()


def test_a_folded_list_still_states_the_whole_count(qapp):
    """What is armed by Select all parts is all of them, not the eight shown."""
    view = _view(qapp, 20)
    try:
        assert "20 parts" in view._detail_widget._parts_meta.text()
    finally:
        view.deleteLater()
        qapp.processEvents()


# -- arming stays where it was -------------------------------------

def test_ticking_a_part_arms_it_without_leaving_the_page(view, qapp):
    row = _parts(view)[0]
    row._check_btn.click()
    for _ in range(6):
        qapp.processEvents()

    assert len(view._model.checked_entities()) == 1
    assert view._detail_widget._name_lbl.text() == "TypeScript", "still here"


def test_the_section_says_how_many_are_armed(view, qapp):
    _parts(view)[0]._check_btn.click()
    for _ in range(6):
        qapp.processEvents()

    assert "1 selected" in view._detail_widget._parts_meta.text()


def test_select_all_parts_arms_every_one(view, qapp):
    view._detail_widget._btn_select_parts.click()
    for _ in range(6):
        qapp.processEvents()

    assert len(view._model.checked_entities()) == 4


def test_a_group_offers_no_way_to_delete_the_folder_it_shares(view):
    """Its path is the whole install; the size in the header is its parts."""
    panel = view._detail_widget

    assert not panel._btn_recycle.isVisibleTo(view)
    assert not panel._check_btn.isVisibleTo(view)
    assert not panel._btn_keep.isVisibleTo(view)
