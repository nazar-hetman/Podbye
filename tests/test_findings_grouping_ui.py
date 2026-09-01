"""An app's fragments are one thing in the list, and nothing is hidden by it.

Measured on a real All-drives scan: 1,241 rows, of which Discord was 23 and
AppData/Local/Packages about 120. Grouping exists so a user can act on those at
once. What it must never do is hide anything — ExampleUser's call was explicitly that
small items stay reachable, because tidying them is something he wants to do.

The shape of the answer changed on 2026-08-24 (see test_two_pane_findings):
a group is a **thing** in the left pane and its fragments are **parts** in the
right one. The rule this file protects is the older one and did not change —
every fragment is still listed, still counted, still selectable.
"""
import pytest

from PySide6.QtCore import QCoreApplication, QEvent

from app.screens.findings_dashboard import CategoryDetailView, _group_risk


def _e(path, name, size=1024, risk="Safe", etype="application_data"):
    return {"path": path, "name": name, "size": f"{size}B", "size_bytes": size,
            "risk": risk, "entity_type": etype, "category": "Application Data",
            "file_count": 1, "reclaimable_bytes": size, "ai_status": "none"}


DISCORD = [
    _e(r"C:\Users\n\AppData\Roaming\discord", "discord", 400),
    _e(r"C:\Users\n\AppData\Roaming\discord\Network", "Network", 300),
    _e(r"C:\Users\n\AppData\Roaming\discord\shared_proto_db", "shared_proto_db", 200),
    _e(r"C:\Users\n\AppData\Roaming\discord\Session Storage", "Session Storage", 100),
]
LONE = _e(r"D:\holiday-photos", "holiday-photos", 9000)


@pytest.fixture
def view(qapp):
    v = CategoryDetailView()
    v._app_index_cache = {}          # no registry in tests — path shape only
    v.resize(1200, 800)
    yield v
    # deleteLater() only *posts* a DeferredDelete, and processEvents() outside
    # a running event loop does not deliver those — so every view this file
    # built stayed alive, and the accumulated widget trees took the whole
    # suite down with a segfault rather than a failure.
    v.close()
    v.setParent(None)
    v.deleteLater()
    qapp.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    qapp.processEvents()


def _things(view):
    return list(view._things_by_key.values())


def _named(view, name):
    return next(t for t in _things(view) if t["name"] == name)


def test_an_apps_fragments_collapse_to_one_thing(view):
    view.set_category("Application Data", DISCORD + [LONE])
    things = _things(view)
    assert len(things) == 2                  # discord + the unrelated lone row
    assert len(_named(view, "discord")["parts"]) == 4


def test_nothing_is_dropped(view):
    """Every entity is reachable as a part of exactly one thing."""
    view.set_category("Application Data", DISCORD + [LONE])
    seen = [e["path"] for t in _things(view) for e in t["parts"]]
    assert sorted(seen) == sorted(e["path"] for e in DISCORD + [LONE])
    assert len(seen) == len(set(seen)), "an entity appeared under two things"


def test_a_thing_reports_the_whole_apps_total(view):
    view.set_category("Application Data", DISCORD + [LONE])
    assert _named(view, "discord")["size_bytes"] == 1000


def test_small_fragments_can_be_acted_on_in_one_go(view):
    """The point of grouping: 363 scattered fragments, one decision."""
    view.set_category("Application Data", DISCORD + [LONE])
    view._select_thing(_named(view, "discord")["key"])
    view._select_all_parts()
    assert len(view._model.checked_entities()) == 4


def test_a_thing_is_never_safer_looking_than_its_worst_part(view):
    view.set_category("Application Data", DISCORD[:2] + [
        _e(r"C:\Users\n\AppData\Roaming\discord\Cookies", "Cookies",
           50, risk="Review")])
    assert _named(view, "discord")["risk"] == "Review"


def test_group_risk_takes_the_most_cautious(view):
    group = {"root": _e("C:/x", "x", risk="Safe"),
             "members": [_e("C:/x/y", "y", risk="Protected")]}
    assert _group_risk(group) == "Protected"
