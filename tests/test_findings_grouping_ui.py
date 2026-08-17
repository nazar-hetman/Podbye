"""The Findings list rolls an app's fragments up under one header.

Measured on a real All-drives scan: 1,241 rows, of which Discord was 23 and
AppData/Local/Packages about 120. What this must NOT do is hide anything —
Nazar's call was explicitly that small items stay reachable, because clearing
them out is something he wants to do. So a group is a way to act on many rows
at once, never a way to make them disappear.
"""
import pytest

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
    from PySide6.QtCore import QCoreApplication, QEvent
    v.close()
    v.setParent(None)
    v.deleteLater()
    qapp.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    qapp.processEvents()


def _rows(view):
    return list(view._display_rows())


def test_an_apps_fragments_collapse_to_one_row(view):
    view.set_category("Application Data", DISCORD + [LONE])
    rows = _rows(view)
    # one header for Discord + the unrelated lone row
    assert len(rows) == 2
    headers = [g for _sr, _e, _d, g in rows if g is not None]
    assert len(headers) == 1
    assert headers[0]["count"] == 4


def test_the_header_carries_the_whole_apps_size(view):
    view.set_category("Application Data", DISCORD)
    _sr, entity, _depth, header = _rows(view)[0]
    assert header["count"] == 4
    assert entity["size_bytes"] == 400 + 300 + 200 + 100


def test_members_are_one_click_away_and_nothing_is_lost(view):
    """Collapsed hides rows from the *view*, never from the data."""
    view.set_category("Application Data", DISCORD)
    collapsed = _rows(view)
    assert len(collapsed) == 1

    view._toggle_group(collapsed[0][3]["key"])
    expanded = _rows(view)

    # header + every one of the four entities
    assert len(expanded) == 5
    names = {e["name"] for _sr, e, depth, grp in expanded if grp is None}
    assert names == {"discord", "Network", "shared_proto_db", "Session Storage"}
    assert all(depth == 1 for _sr, _e, depth, grp in expanded if grp is None)

    view._toggle_group(collapsed[0][3]["key"])
    assert len(_rows(view)) == 1, "toggling twice must return to collapsed"


def test_a_lone_entity_is_never_wrapped_in_a_group(view):
    view.set_category("Application Data", [LONE])
    rows = _rows(view)
    assert len(rows) == 1 and rows[0][3] is None


def test_ticking_the_header_selects_every_member(view):
    """The point of grouping for small items: clear 4 fragments in one click,
    not four."""
    view.set_category("Application Data", DISCORD)
    header_row = view._row_pool[0]
    assert header_row.is_group_header()

    header_row.check_toggled.emit(-1, True)

    checked = [view._model.is_checked(r)
               for r in range(view._model.rowCount())]
    assert all(checked), f"expected every member checked, got {checked}"

    header_row.check_toggled.emit(-1, False)
    assert not any(view._model.is_checked(r)
                   for r in range(view._model.rowCount()))


def test_a_group_is_never_safer_than_its_least_safe_member():
    group = {"root": None, "members": [
        {"risk": "Safe"}, {"risk": "Protected"}, {"risk": "Optional"}]}
    assert _group_risk(group) == "Protected"
    assert _group_risk({"root": None, "members": [{"risk": "Safe"}]}) == "Safe"


def test_a_group_is_ranked_by_its_total_not_its_biggest_member(view):
    """Two 5,000B fragments outrank one 9,000B row, because the header claims
    10,000B. Ranked by first appearance instead, the group sat *below* a row
    it said it was bigger than."""
    big_app = [
        _e(r"C:\Users\n\AppData\Roaming\bigapp", "bigapp", 5000),
        _e(r"C:\Users\n\AppData\Roaming\bigapp\x", "x", 5000),
    ]
    view.set_category("Application Data", [LONE] + big_app)
    rows = _rows(view)
    sizes = [e["size_bytes"] for _sr, e, _d, _g in rows]
    assert sizes == sorted(sizes, reverse=True), (
        "a group must rank by the total it displays")
    assert rows[0][3] is not None and rows[0][3]["count"] == 2
