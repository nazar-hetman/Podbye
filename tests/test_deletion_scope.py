"""One number per finding: what it owns is what it removes.

Two reports, one cause.

    Dev Artifacts "Projects"   header 255.4 GB   ITEMS  8.7 GB
    Browser Data "Chrome Data" header   5.4 GB   CONTENTS 7.4 GB

``_enforce_disjoint_sizes`` charges every scanned byte to exactly one entity:
a nested finding's bytes belong to *it*, not to the folder around it. That is
what makes a category total honest and the storage map add up to the disk.

Deletion was the one place that ignored that decision — a folder-backed entity
went to SHFileOperationW whole, taking rows owned by something else. So one
action carried three numbers: the row said 5.4 GB, CONTENTS measured 7.4 GB,
and 7.4 GB went.

Making everything inclusive instead would have a folder and the nested row
inside it both claiming the same bytes, so category totals — and the storage
map's headline figure — would exceed the disk. The model here is the other
one, and it is the one the accounting already assumed:

    **A finding removes what it owns, and nothing owned by another finding.**

Which collapses the three numbers into one and, because ownership is disjoint,
turns a selection total back into a plain sum.

The consequence worth being deliberate about: recycling a folder that holds a
separately listed finding leaves that finding behind, on disk and in the list.
The folder shell survives because something still lives in it. Nothing is
stranded — low-value entities are suppressed *before* the disjointness pass,
so every subtracted byte belongs to a row the user can still act on.
"""
import os
import shutil
import tempfile

import pytest

from app.models.deletion_scope import (
    covers, deletion_scope_bytes, excluded_paths, expand_targets,
    is_folder_backed, keeps_something_inside, own_bytes, union_scope_bytes,
)
from app.models.smart_entity import SmartEntity

GB = 1024 ** 3
MB = 1024 ** 2


def _chrome():
    """The reported case: a 7.4 GB folder holding a 2.0 GB cache finding, so
    Chrome owns — and removes — 5.4 GB."""
    return {"path": "C:/U/AppData/Local/Google/Chrome", "name": "Chrome Data",
            "entity_type": "browser_profile", "size_bytes": int(5.4 * GB),
            "contained_bytes": int(2.0 * GB), "file_count": 7381,
            "contained_files": 3000,
            "contained_paths":
                ["C:/U/AppData/Local/Google/Chrome/User Data/Default/Cache"]}


def _cache():
    return {"path": "C:/U/AppData/Local/Google/Chrome/User Data/Default/Cache",
            "name": "Chrome cache", "entity_type": "browser_cache",
            "size_bytes": int(2.0 * GB), "file_count": 3000}


def _bucket():
    """A finding that owns *part* of a folder — Work/photos beside
    Work/videos."""
    return {"path": "C:/U/Work", "name": "Loose images in Work",
            "entity_type": "loose_files", "size_bytes": 2 * GB,
            "removable_file_paths": ["C:/U/Work/a.jpg", "C:/U/Work/b.jpg"]}


# ── the ownership decision is recorded, not re-derived ────────────

def test_the_subtraction_records_what_it_moved_and_where():
    """The whole model rests on this being known at detection time, for free,
    at the one place the bytes change hands."""
    from app.services.entity_detector import _enforce_disjoint_sizes

    class Ctx:
        subtree_entity_paths = {"c:/u/chrome", "c:/u/chrome/cache"}

        def subtree(self, norm):
            return ({"c:/u/chrome": (int(7.4 * GB), 10381, 1753),
                     "c:/u/chrome/cache": (int(2.0 * GB), 3000, 400)}[norm]
                    + (0, 0))

    parent = SmartEntity(path="C:/U/Chrome", name="Chrome",
                         entity_type="browser_profile")
    parent.size_bytes, parent.file_count, parent.folder_count = int(7.4 * GB), 10381, 1753
    child = SmartEntity(path="C:/U/Chrome/Cache", name="cache",
                        entity_type="browser_cache")
    child.size_bytes, child.file_count, child.folder_count = int(2.0 * GB), 3000, 400

    _enforce_disjoint_sizes(Ctx(), [parent, child], lambda *_: None)

    assert parent.size_bytes == pytest.approx(5.4 * GB, rel=1e-6)
    assert parent.contained_bytes == pytest.approx(2.0 * GB, rel=1e-6)
    assert parent.contained_paths == ["C:/U/Chrome/Cache"]


def test_the_ownership_survives_a_saved_session():
    """Written by detection, read back by restore — or a resumed session
    silently returns to removing what it does not own."""
    e = SmartEntity(path="C:/U/Chrome", name="Chrome", entity_type="browser_profile")
    e.contained_bytes, e.contained_paths = int(2.0 * GB), ["C:/U/Chrome/Cache"]

    assert e.to_dict()["contained_paths"] == ["C:/U/Chrome/Cache"]


def test_a_plain_folder_owns_everything_in_it():
    plain = {"path": "C:/U/Game", "size_bytes": 40 * GB, "file_count": 900}

    assert excluded_paths(plain) == []
    assert not keeps_something_inside(plain)


# ── one number, everywhere ────────────────────────────────────────

def test_what_is_removed_is_what_is_owned():
    """The property the three-number screen violated."""
    assert deletion_scope_bytes(_chrome()) == own_bytes(_chrome()) == int(5.4 * GB)


def test_a_selection_total_is_a_plain_sum():
    """Disjoint ownership means no two findings can claim the same byte, so
    there is nothing to de-duplicate — including a folder picked together
    with a finding inside it."""
    assert union_scope_bytes([_chrome(), _cache()]) == pytest.approx(7.4 * GB, rel=1e-6)
    assert union_scope_bytes([_chrome()]) == int(5.4 * GB)


def test_the_category_total_still_matches_the_disk():
    """The reason inclusive sizes were rejected: Chrome and its cache would
    both claim the cache's bytes and the map would exceed the drive."""
    assert own_bytes(_chrome()) + own_bytes(_cache()) == pytest.approx(7.4 * GB, rel=1e-6)


# ── a finding never removes another finding ───────────────────────

def test_a_folder_does_not_cover_a_finding_inside_it():
    chrome = _chrome()

    assert covers(chrome, chrome["path"] + "/User Data/Local State")
    assert not covers(chrome, _cache()["path"]), "took a row it does not own"
    assert not covers(chrome, _cache()["path"] + "/data_1")


def test_a_folder_still_covers_everything_it_does_own():
    plain = {"path": "C:/U/Game", "size_bytes": 40 * GB}

    assert covers(plain, "C:/U/Game")
    assert covers(plain, "C:/U/Game/deep/inside.bin")
    assert not covers(plain, "C:/U/GameBeta/x")


def test_a_bucket_never_reaches_beyond_its_own_files():
    """The Work/photos vs Work/videos case: a finding that owns part of a
    folder names its files, so a sibling is untouchable by it."""
    bucket = _bucket()

    assert covers(bucket, "C:/U/Work/a.jpg")
    assert not covers(bucket, "C:/U/Work/holiday.mp4"), "a sibling is in scope"
    assert not covers(bucket, "C:/U/Work"), "the whole folder is in scope"


def test_a_bucket_is_not_folder_backed():
    assert is_folder_backed(_chrome())
    assert not is_folder_backed(_bucket())


# ── the expansion that makes it true on disk ──────────────────────

@pytest.fixture
def tree():
    root = tempfile.mkdtemp(prefix="podbye_scope_")

    def mk(rel, size):
        path = os.path.join(root, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(b"\0" * size)

    mk("Chrome/User Data/Local State", 1 * MB)
    mk("Chrome/User Data/Default/History", 4 * MB)
    mk("Chrome/User Data/Default/Cache/c0.bin", 1 * MB)
    mk("Chrome/User Data/Default/Cache/c1.bin", 1 * MB)
    yield root.replace("\\", "/")
    shutil.rmtree(root, ignore_errors=True)


def test_nothing_excluded_stays_one_operation(tree):
    """The ordinary case must not become a directory crawl."""
    assert expand_targets(tree + "/Chrome", []) == [tree + "/Chrome"]


def test_the_expansion_covers_exactly_what_is_owned(tree):
    cache = tree + "/Chrome/User Data/Default/Cache"

    targets = expand_targets(tree + "/Chrome", [cache])

    assert sorted(targets) == sorted([
        tree + "/Chrome/User Data/Default/History",
        tree + "/Chrome/User Data/Local State"])


def test_the_expansion_only_descends_where_it_must(tree, monkeypatch):
    """Cost is the depth of the exclusion, not the size of the tree — or a
    40,000-file entity would be crawled to build a delete list."""
    calls = []
    real = os.listdir
    monkeypatch.setattr(os, "listdir", lambda p: (calls.append(p), real(p))[1])

    expand_targets(tree + "/Chrome", [tree + "/Chrome/User Data/Default/Cache"])

    assert len(calls) == 3, calls    # Chrome, User Data, Default


def test_an_unreadable_directory_takes_nothing(tree, monkeypatch):
    """Refusing to delete is the safe failure. Falling back to the parent
    because a child could not be listed is the unsafe one."""
    def boom(path):
        raise OSError("denied")

    monkeypatch.setattr(os, "listdir", boom)

    assert expand_targets(tree + "/Chrome",
                          [tree + "/Chrome/User Data/Default/Cache"]) == []


def test_deleting_the_targets_leaves_the_other_finding_alone(tree):
    """End to end, on real files: the bytes charged to the cache row are still
    there afterwards."""
    from app.screens.cleanup_dialog import _cleanup_targets_for_item

    cache = tree + "/Chrome/User Data/Default/Cache"
    chrome = {"path": tree + "/Chrome", "name": "Chrome Data",
              "entity_type": "browser_profile", "size_bytes": 5 * MB,
              "contained_bytes": 2 * MB, "contained_paths": [cache]}

    from app.services.cleanup_engine import CleanupWorker

    targets = _cleanup_targets_for_item(chrome)
    assert sum(t["size_bytes"] for t in targets) == 5 * MB, "total drifted from the row"

    # The expansion moved to the worker thread — building it in the dialog's
    # constructor froze the UI for 2.8 s on a 23k-file tree. Delete what the
    # worker would actually delete, or this stops testing the real path.
    worker = CleanupWorker(
        paths=[t["path"] for t in targets],
        exclude_by_path={t["path"]: list(t.get("cleanup_exclude_paths") or [])
                         for t in targets if t.get("cleanup_exclude_paths")})
    for path in worker._expanded_paths():
        shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)

    survivors = sorted(
        os.path.join(dirpath, name).replace("\\", "/").replace(tree, "")
        for dirpath, _dirs, files in os.walk(tree) for name in files)
    assert survivors == ["/Chrome/User Data/Default/Cache/c0.bin",
                         "/Chrome/User Data/Default/Cache/c1.bin"]


def test_the_contents_section_totals_what_will_go(tree):
    """CONTENTS measured the physical folder, which is why it disagreed with
    the header for the same finding."""
    from app.models.entity_contents import walk_contents

    cache = tree + "/Chrome/User Data/Default/Cache"

    measured = walk_contents(tree + "/Chrome", exclude=[cache])

    assert measured.total_bytes == 5 * MB
    assert walk_contents(tree + "/Chrome").total_bytes == 7 * MB


def test_a_drive_root_is_never_a_target():
    """The expansion must not reintroduce what the safety net removes."""
    from app.screens.cleanup_dialog import _cleanup_targets_for_item

    assert _cleanup_targets_for_item(
        {"path": "C:/", "entity_type": "mixed_folder", "size_bytes": GB}) == []


def test_a_bucket_still_expands_to_its_own_files():
    from app.screens.cleanup_dialog import _cleanup_targets_for_item

    paths = {t["path"] for t in _cleanup_targets_for_item(_bucket())}

    assert paths == {"C:/U/Work/a.jpg", "C:/U/Work/b.jpg"}
    assert "C:/U/Work" not in paths


# ── the list keeps up with what was actually removed ──────────────

def test_a_row_inside_a_folder_that_was_taken_whole_is_dropped():
    """A folder with nothing else's inside it removes everything under it, so
    nothing under it may stay listed — it used to, pointing at a path in the
    bin and still counted in the category total."""
    from app.state.scan_state import ScanState

    state = ScanState()
    parent = SmartEntity(path="C:/U/Chrome", name="Chrome",
                         entity_type="browser_profile")
    inner = SmartEntity(path="C:/U/Chrome/User Data/Default/Cache", name="cache",
                        entity_type="browser_cache")
    elsewhere = SmartEntity(path="C:/U/Firefox", name="Firefox",
                            entity_type="browser_profile")
    state._entities = [parent, inner, elsewhere]

    state.remove_entities_by_path({"C:/U/Chrome"})

    assert {e.path for e in state._entities} == {"C:/U/Firefox"}


def test_a_kept_finding_survives_its_container_being_cleaned():
    """The other half: when the container owned only part of the folder, the
    cleaned paths never include the nested finding, so it stays listed."""
    from app.state.scan_state import ScanState

    state = ScanState()
    parent = SmartEntity(path="C:/U/Chrome", name="Chrome",
                         entity_type="browser_profile")
    inner = SmartEntity(path="C:/U/Chrome/User Data/Default/Cache", name="cache",
                        entity_type="browser_cache")
    state._entities = [parent, inner]

    state.remove_entities_by_path({"C:/U/Chrome/User Data/Local State",
                                   "C:/U/Chrome/User Data/Default/History"})

    assert {e.path for e in state._entities} == {
        "C:/U/Chrome", "C:/U/Chrome/User Data/Default/Cache"}


def test_a_sibling_of_the_cleaned_folder_survives():
    """The prefix test must not catch a name that merely starts the same."""
    from app.state.scan_state import ScanState

    state = ScanState()
    state._entities = [
        SmartEntity(path="C:/U/Chrome", name="Chrome", entity_type="browser_profile"),
        SmartEntity(path="C:/U/ChromeBeta", name="Beta", entity_type="browser_profile")]

    state.remove_entities_by_path({"C:/U/Chrome"})

    assert [e.path for e in state._entities] == ["C:/U/ChromeBeta"]


def test_recycling_named_files_leaves_the_rest_of_the_folder_listed():
    """Partial ownership at the bookkeeping layer: removing the photos must
    not drop the videos finding rooted in the same folder."""
    from app.state.scan_state import ScanState

    state = ScanState()
    photos = SmartEntity(path="C:/U/Work", name="Loose images",
                         entity_type="loose_files")
    photos.size_bytes = 2 * GB
    photos.removable_file_paths = ["C:/U/Work/a.jpg", "C:/U/Work/b.jpg"]
    videos = SmartEntity(path="C:/U/Work", name="Loose videos",
                         entity_type="loose_files")
    videos.size_bytes = 5 * GB
    videos.removable_file_paths = ["C:/U/Work/holiday.mp4"]
    state._entities = [photos, videos]

    state.remove_entities_by_path({"C:/U/Work/a.jpg", "C:/U/Work/b.jpg"})

    assert [e.name for e in state._entities] == ["Loose videos"]


# ── and the screen shows one number ───────────────────────────────

def _inspector(qapp, entity):
    import app.screens.findings_dashboard as fd
    from app.themes.theme_manager import build_qss

    qapp.setStyleSheet(build_qss("forest"))
    view = fd.CategoryDetailView()
    view._app_index_cache = {}
    view.resize(1500, 900)
    view.show()
    view.set_category(entity.get("category", "Browser Data"), [entity])
    for _ in range(8):
        qapp.processEvents()
    view.select_by_path(entity["path"])
    for _ in range(8):
        qapp.processEvents()
    return view


def _displayable(**kw):
    base = {"path": "C:/U/AppData/Local/Google/Chrome", "name": "Chrome Data",
            "entity_type": "browser_profile",
            "entity_type_label": "Browser Profile/Data",
            "size_bytes": int(5.4 * GB), "size": "5.4 GB", "file_count": 7381,
            "folder_count": 1753, "risk": "Review", "category": "Browser Data",
            "actionability": "recycle", "children_sample": [], "ai_status": "none"}
    base.update(kw)
    return base


def test_the_inspector_says_what_stays_rather_than_a_bigger_number(qapp):
    """The line used to warn that the button removed more than the row said.
    It now explains why a folder survives — the only thing left that could
    surprise anyone."""
    view = _inspector(qapp, _displayable(
        contained_bytes=int(2.0 * GB), contained_files=3000,
        contained_paths=["C:/U/AppData/Local/Google/Chrome/User Data/Default/Cache"]))
    try:
        text = view._detail_widget._scope_lbl.text()
        assert view._detail_widget._scope_lbl.isVisibleTo(view)
        assert "kept" in text.lower(), text
        assert "2.0 GB" in text
        assert "7.4 GB" not in text, "the old inclusive figure is back on screen"
    finally:
        view.deleteLater()
        qapp.processEvents()


def test_an_ordinary_folder_gets_no_second_number(qapp):
    view = _inspector(qapp, _displayable())
    try:
        assert not view._detail_widget._scope_lbl.isVisibleTo(view)
    finally:
        view.deleteLater()
        qapp.processEvents()


def test_a_protected_entity_shows_nothing(qapp):
    view = _inspector(qapp, _displayable(
        actionability="protected", risk="Protected",
        contained_paths=["C:/U/AppData/Local/Google/Chrome/User Data/Default/Cache"]))
    try:
        assert not view._detail_widget._scope_lbl.isVisibleTo(view)
    finally:
        view.deleteLater()
        qapp.processEvents()


def test_nested_findings_are_not_called_a_breakdown(qapp):
    """"ITEMS" above a total unrelated to the header was the Projects
    confusion: those rows are separate findings, and they stay."""
    import app.screens.findings_dashboard as fd
    from app.models.entity_contents import MODE_ITEMS
    from app.themes.theme_manager import build_qss

    qapp.setStyleSheet(build_qss("forest"))
    parent = _displayable(name="Projects", path="E:/Projects",
                          entity_type="dev_artifact", category="Dev Artifacts",
                          size_bytes=int(255.4 * GB), size="255.4 GB")
    child = _displayable(name="node_modules", path="E:/Projects/app/node_modules",
                         entity_type="dev_artifact", category="Dev Artifacts",
                         size_bytes=int(8.7 * GB), size="8.7 GB")
    view = fd.CategoryDetailView()
    view._app_index_cache = {}
    view.resize(1500, 900)
    view.show()
    view.set_category("Dev Artifacts", [parent, child])
    for _ in range(8):
        qapp.processEvents()
    view.select_by_path(parent["path"])
    for _ in range(10):
        qapp.processEvents()
    try:
        detail = view._detail_widget
        if detail._contents is not None and detail._contents.mode == MODE_ITEMS:
            assert detail._contents_title.text() != "ITEMS"
            assert "FINDINGS" in detail._contents_title.text()
    finally:
        view.deleteLater()
        qapp.processEvents()
