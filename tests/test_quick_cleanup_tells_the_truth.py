"""Quick Cleanup may only offer what it can do, and report what it did.

Found while reviewing for 1.0:

* **Three categories could never run.** Windows Temp, the Windows Update
  cache and the thumbnail cache all live under a folder named ``Windows``,
  and the cleanup engine refuses every path with that segment. The screen
  measured them, offered them as Safe and counted them in the total, and the
  worker then skipped every item.
* **A skipped category read "already clean".** The per-category result was
  built from succeeded/in-use/failed counts only; protected, kept and
  bin-refused items were not passed in, so a category whose every item was
  refused had all three counters at zero.
* **Per-category "freed" was invented.** The run's total was divided by item
  count, so a category of one 4 GB folder and one of a thousand tiny files
  split the bytes by the thousand.
* **Temp had no age limit.** Everything at the top of %TEMP% was taken,
  including a file created a second earlier by an installer that is still
  running.
* **Temp could be counted twice.** %TEMP% is often the 8.3 short form of
  the same folder %LOCALAPPDATA%\\Temp names in full; the de-duplication
  compared strings.
* **History saved every item's size as 0.**
"""
import os
import time

import pytest

from app.services import quick_cleanup_detector as qcd
from app.services.cleanup_engine import CleanupResult, _is_protected_for_delete
from app.services.cleanup_result_classifier import STATE_ALREADY_CLEAN

DAY = 86400


def _cat(key, label, paths, size=0):
    return qcd.QuickCleanupCategory(key=key, label=label, subtitle="",
                                    paths=list(paths), size_bytes=size,
                                    file_count=len(paths))


# ── only categories the engine will actually clean ────────────────

def test_no_offered_category_is_one_the_engine_refuses():
    names = {fn.__name__ for fn in qcd._SCANNERS}
    assert names.isdisjoint({"_scan_windows_temp", "_scan_windows_update",
                             "_scan_thumbnail_cache"})
    for label in ("Windows Temp", "Windows Update Cache", "Thumbnail Cache"):
        assert label not in qcd.CATEGORY_LABELS


def test_every_path_a_scanner_offers_passes_the_delete_time_guard(tmp_path, monkeypatch):
    local = tmp_path / "Local"
    temp = local / "Temp"
    temp.mkdir(parents=True)
    old = temp / "old.tmp"
    old.write_bytes(b"x" * 10)
    os.utime(old, (time.time() - 30 * DAY,) * 2)
    cache = local / "Google" / "Chrome" / "User Data" / "Default" / "Cache"
    cache.mkdir(parents=True)
    (cache / "f_000001").write_bytes(b"x" * 10)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("TEMP", str(temp))
    monkeypatch.setenv("TMP", str(temp))
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))

    offered = [p for fn in qcd._SCANNERS for c in [fn()] if c for p in c.paths]

    assert offered, "the fixture produced nothing to check"
    assert [p for p in offered if _is_protected_for_delete(p)] == []


# ── Temp: old items only, each folder once ────────────────────────

@pytest.fixture
def temp_dir(tmp_path, monkeypatch):
    local = tmp_path / "Local"
    temp = local / "Temp"
    temp.mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("TEMP", str(temp))
    monkeypatch.setenv("TMP", str(temp))
    return temp


def _aged(path, days):
    t = time.time() - days * DAY
    os.utime(path, (t, t))


def test_a_file_written_moments_ago_is_not_offered(temp_dir):
    fresh = temp_dir / "installer_in_progress.tmp"
    fresh.write_bytes(b"x" * 100)
    old = temp_dir / "leftover.tmp"
    old.write_bytes(b"x" * 100)
    _aged(old, 30)

    cat = qcd._scan_user_temp()

    assert [os.path.basename(p) for p in cat.paths] == ["leftover.tmp"]
    assert cat.size_bytes == 100


def test_a_folder_with_anything_recent_inside_is_not_offered(temp_dir):
    busy = temp_dir / "setup_job"
    busy.mkdir()
    (busy / "old_part.bin").write_bytes(b"x")
    _aged(busy / "old_part.bin", 30)
    (busy / "new_part.bin").write_bytes(b"x")
    _aged(busy, 30)

    cat = qcd._scan_user_temp()

    assert cat is None or all("setup_job" not in p for p in cat.paths)


def test_the_same_temp_folder_is_counted_once(temp_dir, tmp_path, monkeypatch):
    """%TEMP% as a second spelling of %LOCALAPPDATA%\\Temp (a link stands in
    for the 8.3 short name, which Linux does not have)."""
    item = temp_dir / "leftover.tmp"
    item.write_bytes(b"x" * 100)
    _aged(item, 30)
    alias = tmp_path / "SHORTN~1"
    alias.symlink_to(temp_dir, target_is_directory=True)
    monkeypatch.setenv("TEMP", str(alias))

    cat = qcd._scan_user_temp()

    assert len(cat.paths) == 1
    assert cat.size_bytes == 100


# ── the per-category result ───────────────────────────────────────

def test_a_category_whose_items_were_all_refused_is_not_already_clean(qapp):
    from app.screens.quick_cleanup import category_outcomes
    browser = _cat("browser_cache", "Browser Cache", ["C:/b/1", "C:/b/2"])
    result = CleanupResult(skipped_kept=["C:/b/1"],
                           skipped_not_recyclable={"C:/b/2": "bin_disabled"})

    [(cat, moved, assessment)] = category_outcomes([browser], result)

    assert assessment.state != STATE_ALREADY_CLEAN
    assert moved == 0


def test_per_category_bytes_are_what_that_category_moved(qapp):
    from app.screens.quick_cleanup import category_outcomes
    temp = _cat("user_temp", "Temp Files", ["C:/t/big"])
    browser = _cat("browser_cache", "Browser Cache",
                   [f"C:/b/{i}" for i in range(1000)])
    result = CleanupResult(
        succeeded=["C:/t/big"] + [f"C:/b/{i}" for i in range(1000)],
        bytes_by_path={"C:/t/big": 4_000_000_000,
                       **{f"C:/b/{i}": 1_000 for i in range(1000)}},
        total_bytes_freed=4_001_000_000)

    by_key = {c.key: moved for c, moved, _ in category_outcomes([temp, browser], result)}

    assert by_key == {"user_temp": 4_000_000_000, "browser_cache": 1_000_000}


def test_history_records_what_each_item_moved(qapp):
    from app.screens.quick_cleanup import history_items
    temp = _cat("user_temp", "Temp Files", ["C:/t/a", "C:/t/b"])
    result = CleanupResult(succeeded=["C:/t/a"], bytes_by_path={"C:/t/a": 1234},
                           in_use=["C:/t/b"])

    items = {i["path"]: i["size"] for i in history_items([temp], result)}

    assert items == {"C:/t/a": 1234, "C:/t/b": 0}
