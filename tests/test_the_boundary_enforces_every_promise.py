"""The worker that deletes is the one layer every promise has to hold at.

Both cleanup entry points - Quick Cleanup and the Findings confirmation dialog -
hand paths to ``CleanupWorker``, and nothing else in the app moves a user's
files. Protected paths, drive roots, Podbye's own folders and the Recycle Bin
refusals were already enforced there. Three promises were not:

* **Keep, on the target itself.** The dialog filtered kept items out of its
  plan, but Quick Cleanup never asked, and the worker recycled whatever it was
  given. Settings says of a kept path that "cleanup refuses them outright".
* **Keep, on something inside the target.** ``is_kept`` answers for a path and
  its descendants, never its ancestors, so keeping ``Photos/2019`` and then
  cleaning ``Photos`` sent the kept folder to the Recycle Bin with its parent.
* **The cloud acknowledgment.** "I understand this will delete files from my
  cloud account" was enforced only by disabling a button; the auto-confirm
  path called the worker directly and never looked at it.

These tests drive the worker itself, with the shell call replaced, so they
hold whichever screen the paths came from. The UI checks stay as they are:
this is a second layer, not a replacement.
"""
import os

import pytest

from app.services import cleanup_engine, keep_list
from app.services.cleanup_engine import CleanupWorker
from app.services.recycle_bin import RecyclePolicy


@pytest.fixture(autouse=True)
def fresh_keep_list(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    keep_list.reset_for_tests()
    yield
    keep_list.reset_for_tests()


@pytest.fixture(autouse=True)
def bin_accepts_everything(monkeypatch):
    monkeypatch.setattr("app.services.recycle_bin.recycle_bin_policy",
                        lambda path: RecyclePolicy(nuke_on_delete=False,
                                                   max_bytes=None))


@pytest.fixture
def recycled(monkeypatch):
    """Record what would have been sent to the bin, and touch nothing."""
    sent = []
    monkeypatch.setattr(cleanup_engine, "_recycle_one",
                        lambda path: sent.append(_n(path)) or None)
    monkeypatch.setattr(cleanup_engine, "_delete_one",
                        lambda path: (_ for _ in ()).throw(
                            AssertionError(f"permanent delete of {path}")))
    return sent


@pytest.fixture
def no_cloud(monkeypatch):
    monkeypatch.setattr(cleanup_engine, "_cloud_roots", lambda: {}, raising=False)


def _n(path):
    return str(path).replace("\\", "/").rstrip("/").lower()


def _tree(root, *files):
    for rel in files:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * 100)
    return root


def _run(worker):
    captured = {}
    worker.finished.connect(lambda r: captured.setdefault("result", r))
    worker.run()
    return captured["result"]


# ── Keep on the target itself ─────────────────────────────────────

def test_a_kept_target_is_refused_by_the_worker(tmp_path, recycled, no_cloud, qapp):
    photos = _tree(tmp_path / "photos", "a.jpg")
    assert keep_list.keep(str(photos))

    result = _run(CleanupWorker(paths=[str(photos)]))

    assert recycled == []
    assert [_n(p) for p in result.skipped_kept] == [_n(photos)]
    assert result.succeeded == []


def test_a_target_inside_a_kept_folder_is_refused(tmp_path, recycled, no_cloud, qapp):
    photos = _tree(tmp_path / "photos", "2019/a.jpg")
    assert keep_list.keep(str(photos))

    result = _run(CleanupWorker(paths=[str(photos / "2019" / "a.jpg")]))

    assert recycled == []
    assert len(result.skipped_kept) == 1


# ── Keep on something inside the target ───────────────────────────

def test_cleaning_a_parent_leaves_the_kept_folder_inside_it(tmp_path, recycled,
                                                            no_cloud, qapp):
    photos = _tree(tmp_path / "photos", "2019/a.jpg", "2020/b.jpg", "loose.jpg")
    assert keep_list.keep(str(photos / "2019"))

    result = _run(CleanupWorker(paths=[str(photos)]))

    assert _n(photos) not in recycled, "the whole folder, kept child included"
    assert _n(photos / "2019") not in recycled
    assert not any(p.startswith(_n(photos / "2019") + "/") for p in recycled)
    assert set(recycled) == {_n(photos / "2020"), _n(photos / "loose.jpg")}
    assert result.failed == []


def test_a_kept_folder_deep_inside_is_carved_around(tmp_path, recycled,
                                                    no_cloud, qapp):
    root = _tree(tmp_path / "work", "a/b/keep/x.txt", "a/b/other.txt", "c.txt")
    assert keep_list.keep(str(root / "a" / "b" / "keep"))

    _run(CleanupWorker(paths=[str(root)]))

    assert _n(root / "a" / "b" / "keep") not in recycled
    assert _n(root) not in recycled
    assert _n(root / "a") not in recycled
    assert _n(root / "a" / "b") not in recycled
    assert set(recycled) == {_n(root / "a" / "b" / "other.txt"), _n(root / "c.txt")}


def test_keep_and_nested_findings_are_carved_around_together(tmp_path, recycled,
                                                              no_cloud, qapp):
    root = _tree(tmp_path / "proj", "kept/a.txt", "nested/b.txt", "junk/c.txt")
    assert keep_list.keep(str(root / "kept"))

    _run(CleanupWorker(paths=[str(root)],
                       exclude_by_path={str(root): [str(root / "nested")]}))

    assert recycled == [_n(root / "junk")]


def test_a_folder_with_nothing_kept_inside_is_still_one_operation(tmp_path, recycled,
                                                                  no_cloud, qapp):
    root = _tree(tmp_path / "cache", "a.bin", "b/c.bin")
    _run(CleanupWorker(paths=[str(root)]))
    assert recycled == [_n(root)]


# ── the cloud acknowledgment ──────────────────────────────────────

def test_a_cloud_path_is_refused_without_permission(tmp_path, recycled,
                                                    monkeypatch, qapp):
    drive = _tree(tmp_path / "onedrive", "docs/report.docx")
    monkeypatch.setattr(cleanup_engine, "_cloud_roots",
                        lambda: {_n(drive): "OneDrive"}, raising=False)

    result = _run(CleanupWorker(paths=[str(drive / "docs")]))

    assert recycled == []
    assert [_n(p) for p in result.skipped_cloud] == [_n(drive / "docs")]
    assert result.succeeded == []


def test_a_cloud_path_is_recycled_once_the_user_acknowledged_it(tmp_path, recycled,
                                                                monkeypatch, qapp):
    drive = _tree(tmp_path / "onedrive", "docs/report.docx")
    monkeypatch.setattr(cleanup_engine, "_cloud_roots",
                        lambda: {_n(drive): "OneDrive"}, raising=False)

    result = _run(CleanupWorker(paths=[str(drive / "docs")], allow_cloud=True))

    assert recycled == [_n(drive / "docs")]
    assert result.skipped_cloud == []


def test_permission_for_the_cloud_does_not_lift_keep(tmp_path, recycled,
                                                     monkeypatch, qapp):
    drive = _tree(tmp_path / "onedrive", "docs/report.docx")
    monkeypatch.setattr(cleanup_engine, "_cloud_roots",
                        lambda: {_n(drive): "OneDrive"}, raising=False)
    assert keep_list.keep(str(drive / "docs"))

    result = _run(CleanupWorker(paths=[str(drive / "docs")], allow_cloud=True))

    assert recycled == []
    assert len(result.skipped_kept) == 1


# ── what was moved, per path ──────────────────────────────────────

def test_the_result_says_how_many_bytes_each_path_moved(tmp_path, recycled,
                                                        no_cloud, qapp):
    a = tmp_path / "a.bin"
    a.write_bytes(b"x" * 300)
    b = tmp_path / "b.bin"
    b.write_bytes(b"x" * 700)

    result = _run(CleanupWorker(paths=[str(a), str(b)]))

    assert {_n(k): v for k, v in result.bytes_by_path.items()} == {
        _n(a): 300, _n(b): 700}
    assert result.total_bytes_freed == 1000


def test_a_skipped_path_moves_no_bytes(tmp_path, recycled, no_cloud, qapp):
    a = tmp_path / "keep_me.bin"
    a.write_bytes(b"x" * 300)
    assert keep_list.keep(str(a))

    result = _run(CleanupWorker(paths=[str(a)]))

    assert result.bytes_by_path == {}
    assert result.total_bytes_freed == 0


# ── the existing guards still stand ───────────────────────────────

def test_protected_paths_are_still_refused_first(recycled, no_cloud, qapp):
    result = _run(CleanupWorker(paths=["C:/Windows/Temp/x.tmp"]))
    assert recycled == []
    assert result.skipped_protected == ["C:/Windows/Temp/x.tmp"]
