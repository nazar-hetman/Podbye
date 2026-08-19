"""The confirm dialog must state the size it is actually about to remove.

A bucket entity carries its own total, and expanding it into per-file targets
used to copy that total onto every member: nine files selected out of a 240 MB
folder were announced as "9 item(s) · 2.1 GB will be sent to the Recycle Bin".
That is the last sentence a user reads before anything is deleted.
"""
import os

import pytest

from app.screens.cleanup_dialog import _cleanup_targets_for_item


@pytest.fixture
def files(tmp_path):
    made = []
    for i in range(9):
        f = tmp_path / f"blob_{i}.bin"
        f.write_bytes(b"x" * 1_000_000)
        made.append(str(f))
    return made


def _bucket(paths, total=240 * 1024 ** 2):
    return {"path": "C:/cache", "name": "Big cache", "risk": "Optional",
            "category": "Cache & Temp", "entity_type": "cache_folder",
            "actionability": "recycle", "size_bytes": total,
            "reclaimable_bytes": total, "removable_file_paths": paths}


def test_each_file_target_weighs_itself(files):
    targets = _cleanup_targets_for_item(_bucket(files))
    assert len(targets) == 9
    assert sum(t["size_bytes"] for t in targets) == 9_000_000


def test_the_bucket_total_is_not_copied_onto_every_member(files):
    targets = _cleanup_targets_for_item(_bucket(files))
    assert all(t["size_bytes"] != 240 * 1024 ** 2 for t in targets)


def test_reclaimable_follows_the_real_size(files):
    for t in _cleanup_targets_for_item(_bucket(files)):
        assert t["reclaimable_bytes"] == t["size_bytes"]


def test_a_vanished_file_counts_as_nothing_not_as_the_bucket(tmp_path):
    """0 is the honest answer: removing it frees nothing."""
    gone = str(tmp_path / "never_existed.bin")
    targets = _cleanup_targets_for_item(_bucket([gone]))
    assert targets[0]["size_bytes"] == 0


def test_a_folder_backed_entity_keeps_its_own_total(tmp_path):
    """Nothing was expanded, so nothing should be re-measured."""
    entity = _bucket([])
    entity["path"] = str(tmp_path)
    targets = _cleanup_targets_for_item(entity)
    assert len(targets) == 1
    assert targets[0]["size_bytes"] == 240 * 1024 ** 2


def test_the_dialog_states_the_measured_total(qapp, files):
    from app.screens.cleanup_dialog import CleanupConfirmDialog
    dlg = CleanupConfirmDialog([_bucket(files)])
    try:
        assert "9 MB" in dlg._sub_lbl.text(), dlg._sub_lbl.text()
        assert "GB" not in dlg._sub_lbl.text()
    finally:
        dlg.close()
        dlg.deleteLater()
        qapp.processEvents()


def test_the_target_list_drops_blanks_and_repeats(tmp_path):
    """A whitespace entry is not a file, and a repeat would be attempted —
    and reported — twice."""
    real = tmp_path / "a.bin"
    real.write_bytes(b"x" * 10)
    entity = _bucket([str(real), "", "  ", str(real)])
    targets = _cleanup_targets_for_item(entity)
    assert [t["path"] for t in targets] == [str(real)]
