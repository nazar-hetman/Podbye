"""Browsing by folder is the way out when a classification is wrong.

WSL landed under Virtual Machines because two .vhdx images outweighed 619
DLLs; Discord under Media. Neither is findable by category if you don't
already believe the category. By location, both are exactly where the user
expects, because a path is not a judgement call.
"""
import pytest
from PySide6.QtCore import Qt

from app.screens.findings_dashboard import FolderTreeView


def _e(path, size, name=None, risk="Review", category="Unknown"):
    return {"path": path, "size_bytes": size, "file_count": 3,
            "name": name or path.rstrip("\\/").replace("\\", "/").rsplit("/", 1)[-1],
            "risk": risk, "category": category, "entity_type": "unknown_folder"}


ENTITIES = [
    _e(r"C:\Program Files\WSL", 800, category="Virtual Machines"),
    _e(r"C:\Program Files\Git", 200),
    _e(r"C:\Users\n\AppData\Roaming\discord", 600, category="Media"),
    _e(r"D:\photos\2024", 5000),
]


@pytest.fixture
def view(qapp):
    v = FolderTreeView()
    v.resize(1200, 800)
    yield v
    from PySide6.QtCore import QCoreApplication, QEvent
    v.close()
    v.setParent(None)
    v.deleteLater()
    qapp.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    qapp.processEvents()


def _top_level(view):
    tree = view._tree
    return [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]


def test_drives_are_the_top_level_biggest_first(view):
    view.set_entities(ENTITIES)
    names = [i.text(0) for i in _top_level(view)]
    # D: (5000) outranks the C: subtree (1600); C: collapses through its only
    # meaningful chain, so its label carries the path it folded.
    assert len(names) == 2
    assert names[0].startswith("D:")


def test_a_folder_reports_everything_underneath_it(view):
    view.set_entities(ENTITIES)
    tree = view._tree
    c_item = next(i for i in _top_level(view) if i.text(0).startswith("C:"))
    node = c_item.data(0, Qt.UserRole)
    assert node.size_bytes == 800 + 200 + 600


def test_expanding_materialises_the_children(view):
    """Rows are built on expand, not up front — a real scan is ~1,200
    entities across several thousand folders."""
    view.set_entities(ENTITIES)
    c_item = next(i for i in _top_level(view) if i.text(0).startswith("C:"))
    assert c_item.childCount() == 1
    assert c_item.child(0).data(0, Qt.UserRole) is None, "should be a placeholder"

    view._tree.expandItem(c_item)
    kids = [c_item.child(i) for i in range(c_item.childCount())]
    assert all(k.data(0, Qt.UserRole) is not None for k in kids)
    assert len(kids) >= 2


def test_a_misfiled_folder_is_still_where_the_user_expects(view):
    """The whole point: WSL is under Program Files no matter what Vigil
    decided it was."""
    view.set_entities(ENTITIES)
    found = {}

    def walk(item):
        node = item.data(0, Qt.UserRole)
        if node is not None:
            found[node.path.lower()] = node
            if item.childCount() == 1 and item.child(0).data(0, Qt.UserRole) is None:
                view._tree.expandItem(item)
            for i in range(item.childCount()):
                walk(item.child(i))

    for top in _top_level(view):
        view._tree.expandItem(top)
        walk(top)

    assert "c:/program files/wsl" in found
    assert found["c:/program files/wsl"].entity["category"] == "Virtual Machines"


def test_picking_a_folder_that_is_a_finding_emits_it(view, qapp):
    view.set_entities(ENTITIES)
    seen = []
    view.entity_activated.connect(seen.append)

    top = next(i for i in _top_level(view) if i.text(0).startswith("D:"))
    view._on_activated(top, 0)

    assert seen and seen[0]["path"] == r"D:\photos\2024"


def test_a_pure_folder_emits_nothing(view):
    """C:/Program Files is not itself a finding — clicking it must not
    pretend there is something to act on."""
    view.set_entities(ENTITIES)
    seen = []
    view.entity_activated.connect(seen.append)

    c_item = next(i for i in _top_level(view) if i.text(0).startswith("C:"))
    view._tree.expandItem(c_item)
    for i in range(c_item.childCount()):
        child = c_item.child(i)
        if child.data(0, Qt.UserRole).entity is None:
            view._on_activated(child, 0)
    assert seen == []


def test_the_summary_states_the_total(view):
    view.set_entities(ENTITIES)
    text = view._summary_lbl.text()
    assert "4" in text, f"expected the item count in {text!r}"


def test_an_empty_scan_does_not_crash(view):
    view.set_entities([])
    assert view._tree.topLevelItemCount() == 0
