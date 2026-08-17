"""Tests for cleanup flow fixes: post-delete removal, review gating,
protected depth-awareness, and deep-uninstall availability.
"""
from app.screens.cleanup_dialog import _cleanup_targets_for_item, _is_review_tier
from app.screens.findings_dashboard import _has_uninstaller
from app.services.uninstaller import NO_COMMAND, launch_uninstaller
from app.models.findings_table_model import FindingsTableModel


# ── #2: review_only items are now deletable (gated by ack, not refused) ──

def test_review_only_item_now_returns_targets():
    item = {"risk": "Review", "entity_type": "unknown_folder",
            "actionability": "review_only", "path": "C:/stuff/mystery"}
    targets = _cleanup_targets_for_item(item)
    assert len(targets) == 1 and targets[0]["path"] == "C:/stuff/mystery"


def test_protected_item_still_refused():
    item = {"risk": "Protected", "entity_type": "protected_system",
            "actionability": "protected", "path": "C:/Windows"}
    assert _cleanup_targets_for_item(item) == []


def test_is_review_tier():
    assert _is_review_tier({"risk": "Review"})
    assert _is_review_tier({"risk": "Safe", "actionability": "review_only"})
    assert not _is_review_tier({"risk": "Safe"})
    assert not _is_review_tier({"risk": "Optional"})


# ── #1: model drops cleaned rows ──────────────────────────────────

def test_model_remove_cleaned_by_path():
    m = FindingsTableModel()
    m.set_entities([
        {"path": "C:/a", "name": "a"},
        {"path": "C:/b", "name": "b"},
    ])
    removed = m.remove_cleaned(["C:/a"])
    assert removed == 1
    assert m.rowCount() == 1
    assert m.get_entity(0)["path"] == "C:/b"


def test_model_remove_cleaned_by_removable_files():
    m = FindingsTableModel()
    m.set_entities([
        {"path": "C:/", "name": "Loose archives",
         "removable_file_paths": ["C:/x.zip", "C:/y.zip"]},
        {"path": "C:/keep", "name": "keep"},
    ])
    # All of the bucket's files cleaned → bucket removed, root never matched.
    removed = m.remove_cleaned(["C:/x.zip", "C:/y.zip"])
    assert removed == 1
    assert m.get_entity(0)["path"] == "C:/keep"


def test_model_remove_cleaned_partial_keeps_bucket():
    m = FindingsTableModel()
    m.set_entities([
        {"path": "C:/", "name": "Loose archives",
         "removable_file_paths": ["C:/x.zip", "C:/y.zip"]},
    ])
    removed = m.remove_cleaned(["C:/x.zip"])  # only one file gone
    assert removed == 0
    assert m.rowCount() == 1


def test_model_remove_cleaned_partial_shrinks_bucket():
    """A partially-cleaned bucket must drop the recycled files so they don't
    reappear when the bucket is reopened."""
    m = FindingsTableModel()
    m.set_entities([
        {"path": "C:/dl", "name": "Downloads",
         "removable_file_paths": ["C:/dl/a.zip", "C:/dl/b.zip", "C:/dl/c.zip"],
         "file_count": 3},
    ])
    m.remove_cleaned(["C:/dl/a.zip", "C:/dl/b.zip"])
    e = m.get_entity(0)
    assert e["removable_file_paths"] == ["C:/dl/c.zip"]
    assert e["file_count"] == 1


def test_model_remove_cleaned_backslash_paths_match():
    """Recycled paths reported with Windows backslashes must still match."""
    m = FindingsTableModel()
    m.set_entities([
        {"path": "C:/Users/x/AppData/Local/Temp/foo.tmp", "name": "foo.tmp"},
        {"path": "C:/keep", "name": "keep"},
    ])
    removed = m.remove_cleaned([r"C:\Users\x\AppData\Local\Temp\foo.tmp"])
    assert removed == 1
    assert m.get_entity(0)["path"] == "C:/keep"


# ── #4: deep uninstall availability ───────────────────────────────

def test_has_uninstaller(tmp_path):
    """"Registered" is not enough — the file has to be there. 19 of 475
    uninstall commands on a real machine pointed at a deleted executable."""
    real = tmp_path / "unins000.exe"
    real.write_text("")
    assert _has_uninstaller({"uninstall_string": f'"{real}" /S'})
    assert not _has_uninstaller({"uninstall_string": '"C:/nope/unins.exe" /S'})
    assert not _has_uninstaller({"uninstall_string": ""})
    assert not _has_uninstaller({})


def test_launch_uninstaller_empty_is_safe():
    outcome, msg = launch_uninstaller("")
    assert outcome == NO_COMMAND


# ── drive-root guard still holds ──────────────────────────────────

def test_drive_root_never_a_target():
    item = {"risk": "Optional", "entity_type": "archive_group",
            "actionability": "recycle", "path": "C:/"}
    assert _cleanup_targets_for_item(item) == []
