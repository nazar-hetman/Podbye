r"""A whole drive is never something to recycle, on either path to deletion.

The confirm dialog carried the rule — "Safety net: never offer a drive root
(C:/) or empty path as a target" — but applied it only to a folder-backed
entity. Expanding a file list skipped it, so a list containing "C:/" produced a
target of "C:/". And the engine's own backstop, documented as holding
"regardless of what the UI said at selection time", did not cover drive roots
at all: it screens path *segments* (windows, system32, programdata), and a bare
"C:" is not one of those.

No product story reaches either with a drive root — a file list holds files —
so this is defence in depth rather than a live bug. It is also the one input
where being wrong cannot be undone.

Program Files and the user profile are deliberately NOT blocked: Podbye legitimately
cleans inside both, and protection there is per-entity risk.
"""
import pytest

from app.screens.cleanup_dialog import _cleanup_targets_for_item, _is_drive_root_path
from app.services.cleanup_engine import _is_protected_for_delete

ROOTS = ["C:/", "C:\\", "C:", "d:/", "Z:\\", "  C:/  "]


@pytest.mark.parametrize("root", ROOTS)
def test_the_engine_refuses_a_drive_root(root):
    assert _is_protected_for_delete(root) is True


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_the_engine_refuses_an_empty_path(blank):
    assert _is_protected_for_delete(blank) is True


@pytest.mark.parametrize("root", ROOTS)
def test_a_drive_root_inside_a_file_list_never_becomes_a_target(root):
    item = {"path": r"C:\x", "name": "bucket", "risk": "Optional",
            "entity_type": "cache_folder", "actionability": "recycle",
            "size_bytes": 1,
            "removable_file_paths": [r"C:\x\real.tmp", root]}
    paths = [t["path"] for t in _cleanup_targets_for_item(item)]
    assert paths == [r"C:\x\real.tmp"], f"{root!r} survived as {paths}"


def test_a_folder_entity_that_is_a_drive_root_yields_nothing():
    for root in ROOTS:
        item = {"path": root, "name": "drive", "risk": "Optional",
                "entity_type": "cache_folder", "actionability": "recycle",
                "size_bytes": 1, "removable_file_paths": []}
        if _is_drive_root_path(root):
            assert _cleanup_targets_for_item(item) == []


def test_real_files_are_still_deletable():
    """A guard that blocks everything is not a guard."""
    item = {"path": r"C:\x", "name": "bucket", "risk": "Optional",
            "entity_type": "cache_folder", "actionability": "recycle",
            "size_bytes": 1,
            "removable_file_paths": [r"C:\x\a.tmp", r"C:\x\b.tmp"]}
    assert len(_cleanup_targets_for_item(item)) == 2


@pytest.mark.parametrize("path", [
    r"C:\Program Files\SomeApp\cache",
    r"C:\Users\Nazar\Downloads\old.zip",
    r"C:\Users\Nazar\AppData\Local\Temp\x.tmp",
])
def test_places_podbye_is_meant_to_clean_are_not_blocked(path):
    assert _is_protected_for_delete(path) is False
