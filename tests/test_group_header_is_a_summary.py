"""A group must summarise itself, not borrow one member's name.

Reported against a real all-drives scan, about an app whose data sits in both
AppData/Roaming and AppData/Local: *"grouping does not show general info, only
for each folder — roaming and local but no generals"*.

Three things were true of the row at the time: it took its title from whichever
member happened to be the group's root, including the "(Roaming)" the detector
had appended to tell that member apart from its sibling; its subtitle counted
files and never said *where* any of them were; and it was the one row in the
list that could not be inspected, so the figures for the whole app had nowhere
to be shown in full.

The redesign of 2026-08-24 answered the third one structurally — a group is a
thing in the left pane and opening it fills the right one (see
test_two_pane_findings). The first two live here.
"""
import pytest

from PySide6.QtCore import QCoreApplication, QEvent

from app.models.entity_grouping import group_entities, group_label, group_locations
from app.screens.findings_dashboard import CategoryDetailView, _entity_contains_text

ROAMING = r"C:\Users\n\AppData\Roaming\Contoso"
LOCAL = r"C:\Users\n\AppData\Local\Contoso"


def _e(path, name, size=1024, risk="Safe", etype="application_data"):
    return {"path": path, "name": name, "size": f"{size}B", "size_bytes": size,
            "risk": risk, "entity_type": etype, "category": "Application Data",
            "file_count": 1, "folder_count": 0, "reclaimable_bytes": size,
            "ai_status": "none"}


# The detector appends the container name when two rows would otherwise share
# one — which is exactly the case a group exists for.
SPLIT_APP = [
    _e(ROAMING, "Contoso (Roaming)", 400),
    _e(ROAMING + r"\Cache", "Cache", 300),
    _e(LOCAL, "Contoso (Local)", 200),
]


@pytest.fixture
def view(qapp):
    v = CategoryDetailView()
    v._app_index_cache = {}          # no registry in tests — path shape only
    v.resize(1200, 800)
    yield v
    v.close()
    v.setParent(None)
    v.deleteLater()
    qapp.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    qapp.processEvents()


def _group(entities):
    groups = group_entities(entities, {})
    return next(g for g in groups if len(g["members"]) + bool(g["root"]) > 1)


def _thing(view, name):
    return next(t for t in view._things_by_key.values() if t["name"] == name)


# ── the name ──────────────────────────────────────────────────────

def test_the_group_drops_the_members_location_hint():
    assert group_label(_group(SPLIT_APP)) == "Contoso"


def test_a_name_that_really_has_brackets_keeps_them():
    """"Mario15 (GPS, 123 photos)" is a folder name, not a disambiguation."""
    entities = [
        _e(r"D:\Surveys\Mario15 (GPS, 123 photos)", "Mario15 (GPS, 123 photos)"),
        _e(r"D:\Surveys\Mario15 (GPS, 123 photos)\raw", "raw"),
    ]
    group = group_entities(entities, {})[0]
    group["owner"] = r"D:/Surveys/Mario15 (GPS, 123 photos)"
    group["root"] = entities[0]
    assert group_label(group) == "Mario15 (GPS, 123 photos)"


# ── the "where" ───────────────────────────────────────────────────

def test_the_group_lists_the_places_it_covers():
    assert group_locations(_group(SPLIT_APP)) == ["Roaming", "Local"]


def test_a_group_outside_windows_containers_claims_no_location():
    entities = [_e(r"D:\tools\thing", "thing"), _e(r"D:\tools\thing\sub", "sub")]
    assert group_locations(group_entities(entities, {})[0]) == []


def test_the_left_pane_row_says_where(view):
    view.set_category("Application Data", SPLIT_APP)
    meta = _thing(view, "Contoso")["meta"]
    assert "Roaming" in meta and "Local" in meta, meta


def test_a_lone_row_under_appdata_says_where_too():
    """The row never showed a path, so Roaming vs Local was unanswerable."""
    assert "Local" in _entity_contains_text(_e(LOCAL, "Contoso"))


# ── and it can explain the whole of itself ────────────────────────

def test_opening_the_group_explains_all_of_it(view):
    """The third complaint, answered where it is now answered: the group is
    the subject of the inspector, so its own name, location and totals are
    stated in full, with its members listed under them."""
    view.set_category("Application Data", SPLIT_APP)
    view._select_thing(_thing(view, "Contoso")["key"])
    panel = view._detail_widget

    assert panel._name_lbl.text() == "Contoso", "not one member's name"
    assert "3 items" in panel._kind_lbl.text()
    assert "3 parts" in panel._parts_meta.text()
    shown = [r._name_lbl.text() for r in panel._part_row_pool
             if not r.isHidden()]
    assert set(shown) == {"Contoso (Roaming)", "Cache", "Contoso (Local)"}
