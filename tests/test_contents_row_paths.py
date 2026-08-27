"""A contents row has to know where it is.

Reported: clicking an item in Findings said "This file is no longer on disk"
for a file that was right there, in a scan that had just been run.

walk_contents groups files into buckets keyed by a grouping token -
"child:chrome", "rule:cache/cache_data" - and the row took its path by
splitting that key. So the row for the Chrome folder carried the path
"chrome": not a location, a fragment, and lowercased at that. Clicking it
looked the fragment up, found nothing, and reported the failure as the file
having been deleted.

Nothing was ever deleted on the strength of those paths - only MODE_FILES rows
get checkboxes - so this was a wrong message rather than a wrong action.
"""
import os

import pytest

from app.models.entity_contents import MODE_CONTENTS, walk_contents
from app.screens.findings_dashboard import _finding_for_path


@pytest.fixture
def folder(tmp_path):
    """A folder shaped like the ones this grouping exists for."""
    for rel in ("Chrome/Cache/data_0", "Chrome/Cache/data_1",
                "Discord/blob.bin", "loose.txt"):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * 4096)
    return tmp_path


def test_the_rows_are_the_grouped_kind(folder):
    assert walk_contents(str(folder)).mode == MODE_CONTENTS


def test_every_row_with_a_path_points_at_something_real(folder):
    """The bug in one line: the row said "chrome", and nothing is there."""
    for row in walk_contents(str(folder)).rows:
        if not row.path:
            continue
        assert os.path.exists(row.path), (
            f"{row.label!r} carries {row.path!r}, which is not on disk")


def test_the_paths_are_absolute(folder):
    for row in walk_contents(str(folder)).rows:
        if row.path:
            assert os.path.isabs(row.path), f"{row.label!r}: {row.path!r}"


def test_a_row_starts_inside_the_folder_it_describes(folder):
    root = str(folder).replace("\\", "/").rstrip("/").lower()
    for row in walk_contents(str(folder)).rows:
        if row.path:
            assert row.path.replace("\\", "/").lower().startswith(root)


def test_clicking_a_row_finds_the_thing_it_names(folder):
    """What the screen actually does with the path when the row is clicked."""
    rows = [r for r in walk_contents(str(folder)).rows if r.path]
    assert rows, "no addressable rows to check"
    for row in rows:
        assert _finding_for_path(row.path) is not None, (
            f"clicking {row.label!r} would still say the file is gone")


def test_the_other_row_stays_unaddressable(folder):
    """The catch-all row is several folders at once, so it has no path and a
    click on it is ignored rather than guessed at."""
    rows = walk_contents(str(folder)).rows
    for row in rows:
        if not row.path:
            assert row.is_other or not row.named


def test_case_is_preserved_in_the_label_and_the_path(folder):
    """The bucket key is lowercased to group case-variant siblings; that is a
    grouping detail and must not reach the path."""
    rows = {r.label: r for r in walk_contents(str(folder)).rows if r.path}
    assert "Chrome" in rows, f"labels were {sorted(rows)}"
    assert rows["Chrome"].path.endswith("Chrome")


# -- unreadable is not the same as missing ------------------------

def test_a_missing_path_is_still_reported_as_missing(tmp_path):
    assert _finding_for_path(str(tmp_path / "nope.bin")) is None


def test_a_file_whose_metadata_cannot_be_read_is_still_explainable(tmp_path, monkeypatch):
    """A locked or permission-denied file is there; only its size is out of
    reach. Refusing turned "I cannot read the size" into "this does not
    exist", which is a different and untrue statement.
    """
    target = tmp_path / "locked.db"
    target.write_bytes(b"x" * 16)
    real_stat = os.stat

    def _denied(path, *a, **kw):
        if str(path) == str(target):
            raise PermissionError(13, "Access is denied")
        return real_stat(path, *a, **kw)

    monkeypatch.setattr(os, "stat", _denied)
    finding = _finding_for_path(str(target))
    assert finding is not None
    assert finding.path == str(target)
    assert finding.size_bytes == 0
