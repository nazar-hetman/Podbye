"""The right pane is one inspector with two sections, not two windows.

Reported from a screenshot: "2 windows/frames too much". Both panes carried
their own card, so the seam between them was panel edge, splitter handle, panel
edge - three lines, saying "two separate tools" about a group and the part of
it currently selected. What they actually are is one chain: the group, the
parts it is made of, the selected part, and what that part is made of.

So the card moved out to a shell around both, and the panes are drawn on its
surface. The splitter is still a splitter - the handle is now the rule between
the two sections and lights up on hover, so the drag stays discoverable while
doing double duty as the separator.

The cards inside the inspector follow one rule now: a nested list gets a box
because that is what stops it merging into the prose above and below it, and
prose does not. So CONTENTS and DUPLICATE COPIES are boxed; AI REASONING,
DETECTED AS and REMOVAL METHOD are not.

Nothing here changes what is selected, what is listed, or what any of it says.
"""
import tempfile
import time
from pathlib import Path

import pytest
from PySide6.QtCore import Qt

import app.screens.findings_dashboard as fd
from app.themes.theme_manager import build_qss


def _entity(path, name, size, sample=()):
    return {"path": path, "name": name, "entity_type": "dev_artifact",
            "size_bytes": size, "size": fd._format_size(size),
            "file_count": 4, "folder_count": 2, "risk": "Safe",
            "category": "Dev Artifacts",
            "entity_type_label": "Node.js Dependencies",
            "actionability": "recycle", "children_sample": list(sample)}


@pytest.fixture
def view(qapp):
    """A group of four parts, on a real tree so the walk has something to do."""
    qapp.setStyleSheet(build_qss("forest"))
    root = Path(tempfile.mkdtemp()) / "TypeScript"
    entities = []
    for version, size in (("5.8", 9000), ("6.0", 4000),
                          ("5.9", 4000), ("5.7", 3000)):
        for package in ("@types", "types-registry", "@babel"):
            d = root / version / "node_modules" / package
            d.mkdir(parents=True)
            (d / "index.js").write_bytes(b"x" * (size // 3))
        entities.append(_entity(
            f"{str(root).replace(chr(92), '/')}/{version}/node_modules",
            f"npm Packages - {version}", size))
    v = fd.CategoryDetailView()
    v._app_index_cache = {str(root).replace("\\", "/").lower(): "TypeScript"}
    v.set_category("Dev Artifacts", entities)
    v.resize(1150, 720)
    v.show()
    for _ in range(25):
        qapp.processEvents()
    time.sleep(0.9)                      # the contents walk is off-thread
    for _ in range(25):
        qapp.processEvents()
    yield v
    v.deleteLater()
    qapp.processEvents()


# -- one card, not two --------------------------------------------

def test_both_sections_live_in_one_shell(view):
    shell = view._inspector_shell

    assert view._parts_panel.isVisibleTo(shell)
    assert view._right_sidebar.isVisibleTo(shell)


def test_the_shell_is_the_only_thing_drawing_a_card(view):
    assert "border: 1px solid" in view._inspector_shell.styleSheet()


def test_the_parts_pane_no_longer_draws_its_own(view):
    assert "border: none" in view._parts_panel.styleSheet()


def test_the_detail_pane_no_longer_draws_its_own(view):
    assert "border: none" in view._right_sidebar.styleSheet()


def test_the_seam_is_one_rule_with_air_around_it(view):
    """Not two panel edges pressed against a handle."""
    handle = view._right_split.styleSheet()

    assert "handle:vertical" in handle
    assert "height: 1px" in handle
    assert "margin:" in handle


def test_the_rule_answers_the_pointer(view):
    """It is still the drag handle, so it must not look inert."""
    assert "handle:vertical:hover" in view._right_split.styleSheet()


def test_the_shell_survives_a_theme_switch(view, qapp):
    """The card and the rule bake the palette in, like every other block."""
    before = view._inspector_shell.styleSheet()
    qapp.setStyleSheet(build_qss("mono"))
    view._apply_inspector_shell_style()

    assert view._inspector_shell.styleSheet() != before
    assert "border: 1px solid" in view._inspector_shell.styleSheet()


# -- a box where a box groups something -----------------------------

def test_the_contents_breakdown_is_boxed(view):
    """A nested table in a column of prose needs the grouping."""
    assert "1px solid" in view._detail_widget._contents_section.styleSheet()


def test_the_copies_list_is_boxed_for_the_same_reason(view):
    assert "1px solid" in view._detail_widget._dup_section.styleSheet()


def test_the_reasoning_prose_is_not(view):
    """It is a paragraph, not a table - and a box around it read as an empty
    input field, which is what put it in this state."""
    assert "border: none" in view._detail_widget._ai_frame.styleSheet()


# -- and nothing about the behaviour moved --------------------------

def test_the_two_sections_can_still_be_resized(view):
    assert view._right_split.orientation() == Qt.Vertical
    assert not view._right_split.childrenCollapsible()

    view._right_split.setSizes([300, 400])

    assert view._right_split.sizes()[0] > 0


def test_the_parts_are_still_listed(view):
    assert len(view._visible_part_paths()) == 4


def test_selecting_a_part_still_fills_the_inspector(view):
    view._select_source_row(0)
    for _ in range(5):
        view._right_split.parent().update()

    assert view._detail_widget._current_entity.get("name", "").startswith(
        "npm Packages")
