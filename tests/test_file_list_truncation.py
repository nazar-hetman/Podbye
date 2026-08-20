"""A folder bigger than the list can show has to say so, and show the right part.

The listing stopped dead at the 500th directory entry and the tab was then
labelled "Files (500)" as though that were the folder. Two things were wrong.
The count was a fiction — one real folder holds 1,639 photos and says so in its
own name. And the cut happened in scandir order while the list below is sorted
biggest-first, so it read as "the largest things in here": measured on that
folder, 363 of the 500 largest files were not in the list at all.
"""
import os


import pytest

from app.screens.findings_dashboard import _PreallocDetailPanel

# Panels built here are parentless, so nothing ever destroys them. Left alive
# they are re-polished by every later `app.setStyleSheet()` in the suite:
# these three files leaked 6,811 widgets between them, and test_theme_switching
# went from 13 s standalone to ~400 s in the full run because of it.
_BUILT: list = []


@pytest.fixture(autouse=True)
def _destroy_panels_built_here(qapp):
    yield
    from PySide6.QtCore import QCoreApplication, QEvent
    while _BUILT:
        widget = _BUILT.pop()
        try:
            widget.close()
            widget.deleteLater()
        except RuntimeError:
            pass          # already gone
    # deleteLater only *posts* a DeferredDelete, and processEvents outside a
    # running event loop never delivers it.
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    qapp.processEvents()


@pytest.fixture
def photo_folder(tmp_path):
    """30 files whose sizes deliberately do not follow their names."""
    for i in range(30):
        # Biggest are the ones scandir will reach last.
        (tmp_path / f"IMG_{i:04d}.jpg").write_bytes(b"x" * (100 + i * 50))
    return tmp_path


def _panel(qapp, cap=10):
    panel = _PreallocDetailPanel(open_cb=lambda p: None, copy_cb=lambda p: None,
                                 recycle_cb=lambda i: None,
                                 ask_ai_file_cb=lambda p: None)
    panel._FILES_SHOWN_CAP = cap
    _BUILT.append(panel)
    return panel


def _entity(folder):
    return {"path": str(folder), "name": "Photos", "risk": "Review",
            "category": "Media", "entity_type": "photo_collection",
            "actionability": "review_only", "removable_file_paths": []}


def test_the_total_is_the_folder_not_the_slice(qapp, photo_folder):
    panel = _panel(qapp)
    _paths, total, _stats = panel._collect_entity_files(_entity(photo_folder))
    assert total == 30


def test_the_slice_is_the_biggest_files_not_the_first_ones(qapp, photo_folder):
    """scandir order is neither size nor anything a reader would expect."""
    panel = _panel(qapp)
    paths, _total, _stats = panel._collect_entity_files(_entity(photo_folder))
    assert len(paths) == 10
    sizes = sorted((os.path.getsize(p) for p in paths), reverse=True)
    every_size = sorted((os.path.getsize(os.path.join(photo_folder, f))
                         for f in os.listdir(photo_folder)), reverse=True)
    assert sizes == every_size[:10]


def test_the_biggest_file_is_never_left_out(qapp, photo_folder):
    panel = _panel(qapp)
    paths, _total, _stats = panel._collect_entity_files(_entity(photo_folder))
    biggest = max(os.path.join(photo_folder, f) for f in os.listdir(photo_folder))
    assert max(os.path.getsize(p) for p in paths) == max(
        os.path.getsize(os.path.join(photo_folder, f))
        for f in os.listdir(photo_folder))


def test_the_tab_says_how_many_of_how_many(qapp, photo_folder):
    panel = _panel(qapp)
    panel.populate(_entity(photo_folder))
    assert panel._tabs.tabText(1) == "Files (10 of 30)"


def test_a_folder_that_fits_says_only_its_count(qapp, photo_folder):
    panel = _panel(qapp, cap=500)
    panel.populate(_entity(photo_folder))
    assert panel._tabs.tabText(1) == "Files (30)"


def test_the_list_itself_says_it_is_a_slice(qapp, photo_folder):
    """The tab label is small; the claim belongs where the rows are."""
    from app.widgets.controls import ElidedLabel
    panel = _panel(qapp)
    panel.populate(_entity(photo_folder))
    notices = [w.full_text() for w in panel._files_panel._container.findChildren(ElidedLabel)
               if "largest" in w.full_text()]
    assert notices == ["Showing the 10 largest of 30 files"]


def test_no_such_claim_when_the_whole_folder_is_shown(qapp, photo_folder):
    from app.widgets.controls import ElidedLabel
    panel = _panel(qapp, cap=500)
    panel.populate(_entity(photo_folder))
    assert not [w for w in panel._files_panel._container.findChildren(ElidedLabel)
                if "largest" in w.full_text()]


def test_the_sizes_are_carried_over_not_measured_again(qapp, photo_folder):
    """Re-measuring cost 165 ms five milliseconds after the listing read the
    same sizes — and exhausted the panel's stat budget, leaving the tail of the
    list recorded as zero bytes and sorted to the bottom."""
    panel = _panel(qapp)
    panel.populate(_entity(photo_folder))
    files = panel._files_panel
    assert all(files._stats[p][0] > 0 for p in files._all_file_paths)


def test_a_stored_path_list_is_not_re_ordered(qapp, tmp_path):
    """Only a live folder listing gets sliced; an entity that carries its own
    file list is showing exactly what it stands for."""
    paths = []
    for i in range(4):
        f = tmp_path / f"f{i}.zip"
        f.write_bytes(b"x" * (10 - i))
        paths.append(str(f))
    panel = _panel(qapp, cap=2)
    got, total, stats = panel._collect_entity_files(
        {"path": str(tmp_path), "name": "b", "entity_type": "archive_group",
         "removable_file_paths": paths})
    assert got == paths and total == 4 and stats == {}
