"""A row that stands for a list of files should say so, and open on the list.

Asked for: "when we click on 'Downloads archives' the list will show up beneath
the section — in that way user has information per unit and per group".

The per-file view already existed: the detail panel has a paginated Files tab
with a checkbox per file and its own recycle button. Nothing pointed at it, so
"Loose archives in Downloads" read as one indivisible thing. Rather than grow
the findings list into a tree — which would mean teaching sorting, filtering,
selection counts and the arm/checkbox logic about parent and child rows — the
row now says the list exists and selecting it lands there.

The distinction that decides both: an entity backed by a *list* of files
(loose buckets, archive and installer groups) versus one backed by a *folder*
(an app, a game, a photo library). Only the first is a group.
"""
import pytest

from app.screens.findings_dashboard import (
    _entity_file_group_size, _entity_contains_text,
)


def _bucket(n=6, **extra):
    """A loose bucket: its meaning is the file list it was built from."""
    e = {
        "name": "Loose archives in Downloads",
        "path": "C:/Users/u/Downloads",
        "entity_type": "archive_group",
        "entity_type_label": "Archive Files",
        "file_count": n, "folder_count": 0,
        "removable_file_paths": [f"C:/Users/u/Downloads/a{i}.zip" for i in range(n)],
    }
    e.update(extra)
    return e


def _folder(**extra):
    """A folder-backed entity: its meaning is the folder, not a list."""
    e = {
        "name": "Mission Planner 1",
        "path": "C:/Users/u/Downloads/Mission Planner 1",
        "entity_type": "download_item",
        "entity_type_label": "Downloaded Item",
        "file_count": 812, "folder_count": 40,
        "removable_file_paths": [],
    }
    e.update(extra)
    return e


# ── which entities are groups ─────────────────────────────────────

def test_a_loose_bucket_is_a_group():
    assert _entity_file_group_size(_bucket(6)) == 6


def test_a_folder_backed_entity_is_not():
    """812 files inside, but none of them individually actionable."""
    assert _entity_file_group_size(_folder()) == 0


def test_a_single_file_entity_is_not_a_group():
    """One installer is not a list to pick from."""
    single = _bucket(1, name="Installer (VSCodeUserSetup)", entity_type="installer")
    assert _entity_file_group_size(single) == 1


@pytest.mark.parametrize("value", [None, [], ["", None]])
def test_missing_or_empty_lists_are_handled(value):
    assert _entity_file_group_size(_folder(removable_file_paths=value)) == 0


# ── what the row says ─────────────────────────────────────────────

def test_a_group_row_advertises_the_file_list():
    text = _entity_contains_text(_bucket(6))
    assert "choose individual files" in text
    assert "6 files" in text, "dropped the existing content summary"


def test_a_group_row_says_what_the_files_are():
    """The type label used to sit here; the file kinds took its place.

    "Loose archives in Downloads · 6 files · Archive Files" says "archives"
    twice and names nothing. Reported as: it does not show what is proposed to
    delete. The extensions are what a row has room to answer that with.
    """
    mixed = _bucket(4)
    mixed["removable_file_paths"] = [
        "C:/Users/u/Downloads/a.zip", "C:/Users/u/Downloads/b.zip",
        "C:/Users/u/Downloads/c.pdf", "C:/Users/u/Downloads/d.csv",
    ]
    text = _entity_contains_text(mixed)
    assert "2 ZIP" in text
    assert "1 PDF" in text and "1 CSV" in text


def test_a_folder_row_is_left_alone():
    text = _entity_contains_text(_folder())
    assert "choose individual files" not in text
    assert "812 files" in text and "40 folders" in text


def test_a_single_file_row_makes_no_such_offer():
    single = _bucket(1, name="Installer (setup)", entity_type="installer")
    assert "choose individual files" not in _entity_contains_text(single)


def test_duplicates_keep_their_own_subtitle():
    dup = _bucket(4, entity_type="duplicate_group")
    assert "choose individual files" not in _entity_contains_text(dup)


@pytest.mark.parametrize("code", ["uk", "fr"])
def test_the_cue_is_translated(code):
    import io, json, pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "app" / "locales"
    table = json.load(io.open(root / f"{code}.json", encoding="utf-8"))
    assert table.get("choose individual files"), f"{code}: cue is untranslated"


# ── where selecting one lands ─────────────────────────────────────

# ── the list is on the page, not behind a tab ───────────────

def test_a_bucket_shows_its_files_in_the_contents_section():
    """The tab this file was written for is gone.

    It existed because the per-file list lived behind "Files", and the row had
    to advertise it. The list is now on the main page, so what matters is that
    the section chooses the file representation rather than the folder one.
    """
    from app.models.entity_contents import MODE_FILES, mode_for
    assert mode_for(_bucket(6)) == MODE_FILES


def test_a_folder_backed_entity_gets_components_not_files():
    from app.models.entity_contents import MODE_CONTENTS, mode_for
    assert mode_for(_folder()) == MODE_CONTENTS


def test_a_single_file_gets_no_section_at_all():
    """"Steam contains Steam" is the redundancy the redesign removed."""
    from app.models.entity_contents import MODE_NONE, mode_for
    single = _bucket(1, name="Installer (setup)", entity_type="installer")
    assert mode_for(single) == MODE_NONE
