"""The inspector answers the delete question without a second click.

The order it has to answer in, from the brief: what is this, what exactly will
be removed, what is inside it, what happens if I remove it, what can I do. The
old panel answered the first with a seven-row CATEGORY:/TYPE:/PATH:/SIZE:
table and the rest behind a Files tab.

The distinction this file guards hardest is the one with different deletion
semantics: **one entity containing components** (they go with it, nothing to
tick) versus **one convenience group of independently removable files** (tick
what you want). Getting that wrong means either a checkbox that lies or a
deletion the user did not agree to.
"""
import pytest

from PySide6.QtCore import QCoreApplication, QEvent

from app.screens.findings_dashboard import CategoryDetailView


def _entity(path, name, **over):
    base = {"path": path, "name": name, "size": "12 MB",
            "size_bytes": 12 * 1024 ** 2, "risk": "Safe",
            "entity_type": "cache_folder", "category": "Cache & Temp",
            "file_count": 40, "folder_count": 6, "ai_status": "none",
            "reclaimable_bytes": 12 * 1024 ** 2, "semantic_label": "Cache Folder",
            "first_seen": "Aug 20", "modified": 1_760_000_000.0,
            "accessed": 1_760_000_000.0}
    base.update(over)
    return base


@pytest.fixture
def view(qapp):
    v = CategoryDetailView()
    v._app_index_cache = {}
    v.resize(1400, 800)
    yield v
    v.stop_background_work()
    v.close()
    v.setParent(None)
    v.deleteLater()
    qapp.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    qapp.processEvents()


def _panel(view, entity):
    view.set_category("Cache & Temp", [entity])
    view._show_detail_sidebar(entity)
    return view._right_sidebar.detail_widget


def _rows(panel):
    return [r for r in panel._content_row_pool if not r.isHidden()]


# ── what is this ──────────────────────────────────────────────────

def test_the_identity_is_four_lines_not_a_property_table(view):
    panel = _panel(view, _entity("C:/x/cache", "Cache"))
    assert panel._name_lbl.text() == "Cache"
    assert panel._kind_lbl.text() == "Cache Folder"
    assert panel._path_lbl.text() == "C:/x/cache"
    assert "40 files" in panel._scale_lbl.text()
    assert "6 folders" in panel._scale_lbl.text()


def test_the_property_table_is_gone(view):
    panel = _panel(view, _entity("C:/x/cache", "Cache"))
    for gone in ("_cat_key", "_lbl_key", "_size_key", "_items_key",
                 "_activity_key", "_importance_key"):
        assert not hasattr(panel, gone), gone


def test_last_active_is_only_claimed_when_it_is_a_fact(view):
    """Windows disables last-access updates by default.

    Measured on the reporting machine: fsutil reports DisableLastAccess=1 and
    82% of entities have an access time within a day of their modification
    time, median gap 0.0 days. Printing "last active" regardless dresses the
    modification date up as something it is not.
    """
    same = _entity("C:/x/a", "A", modified=1_760_000_000.0,
                   accessed=1_760_000_050.0, last_access="Oct 9")
    assert "last active" not in _panel(view, same)._scale_lbl.text()

    later = _entity("C:/x/b", "B", modified=1_700_000_000.0,
                    accessed=1_760_000_000.0, last_access="Oct 9")
    assert "last active" in _panel(view, later)._scale_lbl.text()


# ── what is inside, on the page ───────────────────────────────────

def test_a_file_collection_lists_its_files_without_a_tab(view, tmp_path):
    first = tmp_path / "big.zip"
    first.write_bytes(b"x" * 900)
    second = tmp_path / "small.zip"
    second.write_bytes(b"x" * 10)
    entity = _entity(str(tmp_path), "Loose archives",
                     removable_file_paths=[str(first), str(second)])

    panel = _panel(view, entity)
    assert not panel._contents_section.isHidden()
    assert panel._contents_title.text() == "FILES"
    assert {r._name.text() for r in _rows(panel)} == {"big.zip", "small.zip"}


def test_nothing_worth_saying_means_no_section(view):
    """"Steam contains Steam" \u2014 the redundancy this replaces."""
    single = _entity("C:/x/one.zip", "one.zip", file_count=1, folder_count=0,
                     removable_file_paths=["C:/x/one.zip"])
    assert _panel(view, single)._contents_section.isHidden()


def test_the_folder_summary_arrives_without_being_asked(view, tmp_path):
    (tmp_path / "steamapps" / "common").mkdir(parents=True)
    (tmp_path / "steamapps" / "common" / "game.pak").write_bytes(b"x" * 5000)
    entity = _entity(str(tmp_path), "Steam", children_sample=["steamapps"])

    panel = _panel(view, entity)
    worker = panel._contents_worker
    assert worker is not None, "no measurement was started"
    worker.wait(4000)
    QCoreApplication.processEvents()

    assert panel._contents_title.text() == "CONTENTS"
    assert "Installed games" in {r._name.text() for r in _rows(panel)}


# ── the two deletion semantics look different ─────────────────────

def test_files_can_be_ticked_one_by_one(view, tmp_path):
    a = tmp_path / "a.zip"
    a.write_bytes(b"x")
    b = tmp_path / "b.zip"
    b.write_bytes(b"x")
    entity = _entity(str(tmp_path), "Loose archives",
                     removable_file_paths=[str(a), str(b)])
    panel = _panel(view, entity)
    # isHidden(), not isVisible(): nothing here has been shown on screen, and
    # an unshown widget is never "visible" however it was configured.
    assert all(not r._check.isHidden() for r in _rows(panel))


def test_components_cannot_be_ticked(view, tmp_path):
    """They go when the folder goes; a checkbox would say otherwise."""
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "f").write_bytes(b"x" * 100)
    panel = _panel(view, _entity(str(tmp_path), "App",
                                 children_sample=["sub"]))
    assert all(r._check.isHidden() for r in _rows(panel))


# ── what happens if I remove it ───────────────────────────────────

def test_no_consequence_line_when_the_table_already_said_it(view, tmp_path):
    """A sentence restating the rows above it is filler, not information."""
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "f").write_bytes(b"x" * 100)
    panel = _panel(view, _entity(str(tmp_path), "App",
                                 children_sample=["sub"]))
    worker = panel._contents_worker
    if worker is not None:
        worker.wait(4000)
        QCoreApplication.processEvents()
    assert panel._consequence_lbl.isHidden()


def test_a_consequence_that_adds_something_is_shown(view, tmp_path):
    """Cloud sync leaves the machine; no contents table can show that."""
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "f").write_bytes(b"x" * 100)
    panel = _panel(view, _entity(str(tmp_path), "App",
                                 children_sample=["sub"],
                                 cloud_sync_provider="OneDrive"))
    assert not panel._consequence_lbl.isHidden()
    assert "OneDrive" in panel._consequence_lbl.text()


# ── the tail is secondary, not hidden ─────────────────────────────

def test_a_long_list_folds_but_says_how_much(view, tmp_path):
    paths = []
    for i in range(9):
        f = tmp_path / f"f{i}.zip"
        f.write_bytes(b"x" * (100 - i))
        paths.append(str(f))
    panel = _panel(view, _entity(str(tmp_path), "Many",
                                 removable_file_paths=paths))
    assert len(_rows(panel)) == panel._CONTENT_ROWS_SHOWN
    assert "4" in panel._btn_contents_more.text()

    panel._on_contents_more()
    assert len(_rows(panel)) == 9


# ── the walk never outlives the widget ────────────────────────────

def test_the_measurement_stops_on_request(view, tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "f").write_bytes(b"x" * 10)
    panel = _panel(view, _entity(str(tmp_path), "App"))
    view.stop_background_work()
    assert panel._contents_worker is None


def test_the_widget_can_be_collected_while_the_walk_runs(qapp, tmp_path):
    """The crash this cost a debugging session to find.

    A QThread parented to a widget is destroyed with it, and destroying a
    *running* one calls std::terminate \u2014 Windows reports 0xC0000409 and the
    process vanishes with no traceback. It showed up as pytest printing dots
    and stopping at 4% with a clean exit code, because a test let its sidebar
    fall out of scope while a real 40 GB folder was still being measured.

    So the walk is never parented to the panel. Dropping every reference to
    the widget mid-walk has to be survivable.
    """
    import gc

    from app.screens.findings_dashboard import RightSidebar, _LIVE_CONTENT_WALKS

    for i in range(24):
        sub = tmp_path / f"dir{i}"
        sub.mkdir()
        for j in range(40):
            (sub / f"f{j}.bin").write_bytes(b"x" * 512)

    side = RightSidebar(open_cb=lambda p: None, copy_cb=lambda p: None)
    side.populate(_entity(str(tmp_path), "Big", children_sample=["dir0"]))
    assert side.detail_widget._contents_worker is not None

    del side                      # exactly what the test suite did
    gc.collect()
    qapp.processEvents()

    for worker in list(_LIVE_CONTENT_WALKS):
        worker.wait(5000)
    qapp.processEvents()
