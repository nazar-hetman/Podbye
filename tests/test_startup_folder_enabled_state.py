"""A Startup-folder shortcut that Windows has switched off must read as off.

Startup-folder entries reported enabled=True unconditionally. Turning one off
in Task Manager does not delete the shortcut - it records the decision under
Explorer\\StartupApproved\\StartupFolder, exactly as it does for a disabled Run
entry. Podbye ignored that key for folders, so a disabled item was listed as
running at login.

Read-only: this reads the approval map, it does not write one. Nothing here
touches the real registry or the real Startup folder - the approval map is
supplied directly and the folder is built in tmp_path.
"""
import pytest

from app.services import startup_detector as sd


def _shortcut(folder, name):
    """A .lnk file. Its target does not resolve, which is fine - this is about
    the enabled flag, and an unresolved target is a state the reader already
    handles."""
    p = folder / name
    p.write_bytes(b"L\x00\x00\x00" + b"\x00" * 64)
    return p


@pytest.fixture
def startup_folder(tmp_path):
    d = tmp_path / "Startup"
    d.mkdir()
    for name in ("Grammarly.lnk", "OneDrive.lnk", "Notes.lnk"):
        _shortcut(d, name)
    return d


def _read(folder, approved):
    return sd._read_startup_folder(str(folder), "startup_folder",
                                   "User startup folder", approved)


def _by_name(entries):
    return {e.name: e for e in entries}


def test_an_approved_shortcut_is_enabled(startup_folder):
    # byte 0 == 0x02 is how Windows records "enabled"
    approved = {"grammarly.lnk": True, "onedrive.lnk": True, "notes.lnk": True}
    entries = _by_name(_read(startup_folder, approved))
    assert all(e.enabled for e in entries.values()), entries


def test_a_disabled_shortcut_is_reported_disabled(startup_folder):
    """The bug: this used to come back enabled."""
    approved = {"grammarly.lnk": False, "onedrive.lnk": True, "notes.lnk": True}
    entries = _by_name(_read(startup_folder, approved))
    assert entries["Grammarly"].enabled is False
    assert entries["OneDrive"].enabled is True
    assert entries["Notes"].enabled is True


def test_an_entry_absent_from_the_map_is_enabled(startup_folder):
    """Windows only writes an entry once someone has toggled it, so absent
    means untouched, which means enabled - the same default the Run reader
    uses."""
    entries = _by_name(_read(startup_folder, {"grammarly.lnk": False}))
    assert entries["Grammarly"].enabled is False
    assert entries["OneDrive"].enabled is True
    assert entries["Notes"].enabled is True


def test_no_approval_map_at_all_leaves_everything_enabled(startup_folder):
    """A machine with the key missing must behave as it always did."""
    for approved in (None, {}):
        entries = _by_name(_read(startup_folder, approved))
        assert all(e.enabled for e in entries.values()), approved


def test_the_lookup_uses_the_filename_not_the_stem(startup_folder):
    """Windows keys this map by "Grammarly.lnk", not "Grammarly".

    Matching on the stem would silently never find anything, and every item
    would read as enabled again - the original bug, with a lookup in front
    of it.
    """
    by_stem = {"grammarly": False}
    entries = _by_name(_read(startup_folder, by_stem))
    assert entries["Grammarly"].enabled is True, (
        "a stem-keyed map must not match; the real key carries .lnk")

    by_filename = {"grammarly.lnk": False}
    entries = _by_name(_read(startup_folder, by_filename))
    assert entries["Grammarly"].enabled is False


def test_the_name_match_is_case_insensitive(startup_folder):
    """Registry value names keep their case; the reader lowercases both sides."""
    entries = _by_name(_read(startup_folder, {"grammarly.lnk": False}))
    assert entries["Grammarly"].enabled is False


def test_the_entry_is_otherwise_unchanged(startup_folder):
    """Only the flag moved. Source, label and name still describe the item."""
    entries = _by_name(_read(startup_folder, {"grammarly.lnk": False}))
    e = entries["Grammarly"]
    assert e.source == "startup_folder"
    assert e.source_label == "User startup folder"
    assert e.key == "startup_folder|grammarly"


def test_a_missing_folder_is_not_an_error(tmp_path):
    assert sd._read_startup_folder(str(tmp_path / "nope"), "startup_folder",
                                   "User startup folder", {}) == []


def test_detection_stays_read_only():
    """No write path may appear by accident alongside this reader."""
    import inspect

    src = inspect.getsource(sd)
    for forbidden in ("SetValueEx", "DeleteValue", "CreateKey", "KEY_WRITE",
                      "KEY_SET_VALUE"):
        assert forbidden not in src, f"{forbidden} appeared in the detector"
