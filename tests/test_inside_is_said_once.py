"""The right pane says what is inside a thing once, not twice.

Reported from a screenshot: two panels stacked, both reading as "what is in
here". They are different questions - the top one lists a *group's parts*, each
its own decision with its own checkbox; the inspector's section lists what the
*selected part* is made of, none of it separately removable - but in one common
shape they answered with the same rows.

Reproduced from the entity graph rather than the picture:

    group 'NVIDIA Corporation' parts = [Nsight, Python Env - Lib, Python Env - lib]
    select Nsight  ->  ITEMS = [Python Env - Lib, Python Env - lib]

The same two rows, same names, same sizes, one panel apart. It happens because
an entity's children are usually members of the entity's own group, and
``items_summary`` had no idea anything else was on screen.

Excluding them is not enough on its own. A folder whose children are entities
falls through to the measured breakdown, and that buckets by top-level child -
so a workspace's two projects came straight back as components. Hence the
second rule: a section that would only repeat the panel above does not appear
at all, and a section that adds even one row of its own is shown whole, with
its real numbers. Dropping the repeated rows individually was rejected - they
are usually the biggest thing in the folder, and a breakdown that omits 400 of
405 MB is not shorter, it is wrong.
"""
import tempfile
import time
from pathlib import Path

import pytest

import app.screens.findings_dashboard as fd
from app.models.entity_contents import items_summary
from app.themes.theme_manager import build_qss


def _entity(path, name, etype, size, files=2, folders=2, sample=()):
    return {"path": path, "name": name, "entity_type": etype,
            "size_bytes": size, "size": fd._format_size(size),
            "file_count": files, "folder_count": folders, "risk": "Safe",
            "category": "Dev Artifacts", "entity_type_label": "Folder",
            "actionability": "recycle", "children_sample": list(sample)}


PARENT = "C:/PF/Vendor/App"


def _world():
    return [_entity(PARENT, "App", "application", 655),
            _entity(f"{PARENT}/ProjectA", "ProjectA", "dev_project", 400),
            _entity(f"{PARENT}/ProjectB", "ProjectB", "dev_project", 250)]


# -- the data rule, without a screen -------------------------------

def test_items_can_be_told_what_is_already_shown():
    world = _world()

    everything = items_summary(world[0], world)
    minus_one = items_summary(world[0], world, exclude=[f"{PARENT}/ProjectA"])

    assert [r.label for r in everything.rows] == ["ProjectA", "ProjectB"]
    assert [r.label for r in minus_one.rows] == ["ProjectB"]


def test_excluding_everything_leaves_no_section():
    world = _world()

    assert not items_summary(world[0], world,
                             exclude=[f"{PARENT}/ProjectA",
                                      f"{PARENT}/ProjectB"])


def test_the_exclusion_is_case_and_slash_insensitive():
    """Scanned paths mix separators inside one string."""
    world = _world()

    trimmed = items_summary(world[0], world,
                            exclude=["c:\\pf\\vendor\\app\\projecta\\"])

    assert [r.label for r in trimmed.rows] == ["ProjectB"]


def test_the_total_is_recomputed_from_what_is_listed():
    """The section's own total, so the number matches the rows under it."""
    world = _world()

    trimmed = items_summary(world[0], world, exclude=[f"{PARENT}/ProjectA"])

    assert trimmed.total_bytes == 250


# -- the screen, on a real directory tree --------------------------

def _settle(qapp):
    for _ in range(25):
        qapp.processEvents()
    time.sleep(0.9)                      # the contents walk is off-thread
    for _ in range(25):
        qapp.processEvents()


def _view(qapp, root, entities):
    qapp.setStyleSheet(build_qss("forest"))
    view = fd.CategoryDetailView()
    view._app_index_cache = {root.lower(): "Vendor"}
    view.set_category("Dev Artifacts", entities)
    view.resize(1150, 700)
    view.show()
    _settle(qapp)
    return view


@pytest.fixture
def shallow(qapp):
    """A container whose children sit directly inside it."""
    root = Path(tempfile.mkdtemp()) / "VendorApp"
    for name, size in (("ProjectA", 4000), ("ProjectB", 2500)):
        (root / name / "src").mkdir(parents=True)
        (root / name / "src" / "big.bin").write_bytes(b"x" * size)
    (root / "readme.txt").write_bytes(b"y" * 50)
    r = str(root).replace("\\", "/")
    view = _view(qapp, r, [
        _entity(r, "Vendor App", "application", 6550,
                sample=["ProjectA", "ProjectB"]),
        _entity(f"{r}/ProjectA", "ProjectA", "dev_project", 4000),
        _entity(f"{r}/ProjectB", "ProjectB", "dev_project", 2500)])
    yield view
    view.deleteLater()
    qapp.processEvents()


@pytest.fixture
def deep(qapp):
    """A container whose child entities are buried several levels down."""
    root = Path(tempfile.mkdtemp()) / "Nsight"
    for host in ("target-windows-x64", "host-windows-x64"):
        d = root / host / "python" / "Lib" / "venv"
        d.mkdir(parents=True)
        (d / "pyvenv.cfg").write_bytes(b"z" * 4100)
    r = str(root).replace("\\", "/")
    view = _view(qapp, r, [
        _entity(r, "Nsight", "application", 8200,
                sample=["target-windows-x64", "host-windows-x64"]),
        _entity(f"{r}/target-windows-x64/python/Lib/venv", "Python Env - Lib",
                "dev_artifact", 4100),
        _entity(f"{r}/host-windows-x64/python/Lib/venv", "Python Env - lib",
                "dev_artifact", 4100)])
    yield view
    view.deleteLater()
    qapp.processEvents()


def _section_rows(view):
    contents = view._detail_widget._contents
    return [r.label for r in (contents.rows if contents else [])]


def test_the_parts_panel_is_what_the_inspector_reads(shallow):
    assert len(shallow._visible_part_paths()) == 3


def test_a_container_does_not_relist_its_own_parts(shallow):
    """The reported duplication: the two projects are already above."""
    assert shallow._detail_widget._current_entity["name"] == "Vendor App"
    assert "ProjectA" not in _section_rows(shallow)
    assert "ProjectB" not in _section_rows(shallow)


def test_the_section_goes_away_rather_than_lying_about_the_size(shallow):
    """Nothing left to say, so nothing is said - the alternative was a
    breakdown that omitted the two biggest things in the folder."""
    assert not shallow._detail_widget._contents_section.isVisibleTo(shallow)


def test_a_breakdown_that_says_something_new_still_appears(deep):
    """The children are five levels down, so the top-level split is not a
    repeat of anything."""
    assert deep._detail_widget._contents_section.isVisibleTo(deep)
    assert sorted(_section_rows(deep)) == ["host-windows-x64",
                                           "target-windows-x64"]


def test_with_no_panel_above_nothing_is_suppressed(qapp):
    """The inspector is not always under a parts list, and must not behave as
    though it were."""
    panel = fd._PreallocDetailPanel(open_cb=lambda p: None,
                                    copy_cb=lambda p: None)

    assert panel._shown_as_parts() == ()


def test_a_broken_callback_does_not_take_the_inspector_down(qapp):
    def _boom():
        raise RuntimeError("the panel went away mid-populate")

    panel = fd._PreallocDetailPanel(open_cb=lambda p: None,
                                    copy_cb=lambda p: None, parts_cb=_boom)

    assert panel._shown_as_parts() == ()


# -- the headings no longer ask the same question ------------------

def test_the_two_headings_are_different_questions(shallow):
    """WHAT IS INSIDE X over CONTENTS read as one question asked twice."""
    assert shallow._parts_title_lbl.text() == "PARTS OF"
    assert shallow._detail_widget._contents_title.text() != "PARTS OF"


def test_the_selected_part_still_gets_its_own_breakdown(deep):
    """Suppression is about repetition, not about hiding the section: when it
    has something of its own to say it says it, under its own heading."""
    assert deep._detail_widget._contents_title.text() == "CONTENTS"
