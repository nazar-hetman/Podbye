"""What is inside an entity, said on the main view rather than behind a tab.

The inspector answered "what am I deleting?" with a seven-row property table
and a separate Files tab, so the contents of a 160 GB folder were one click
away from a delete button — and a click nobody makes is a click that does not
happen.

Three measurements shaped what this module could promise, all taken on the
reporting machine's real drives:

* A full walk of Steam's 40,349 files takes ~640 ms; ``E:/My Projects`` does
  not finish inside 700 ms at all. So the walk is budgeted, off the UI thread,
  and says when it was cut short.
* The median entity takes 2 ms and the 90th percentile 338 ms, so the common
  case is free and only the outliers need the budget.
* Of 1,200 entities in a real session, 67 carry an explicit file list and the
  longest holds 25 files. There is no paging problem to solve.
"""
import os

import pytest

from app.models.entity_contents import (
    MODE_CONTENTS, MODE_FILES, MODE_NONE, Contents, ContentRow,
    measure_files, mode_for, quick_summary, removal_consequence, rule_for,
    walk_contents,
)

MB = 1024 * 1024


def _entity(**over):
    base = {"path": "C:/App", "name": "App", "size": "1 GB",
            "size_bytes": 1024 ** 3, "file_count": 40, "folder_count": 6,
            "entity_type": "application", "risk": "Optional"}
    base.update(over)
    return base


# ── which representation ──────────────────────────────────────────

def test_a_collection_of_files_shows_its_files():
    entity = _entity(removable_file_paths=["C:/d/a.zip", "C:/d/b.zip"])
    assert mode_for(entity) == MODE_FILES


def test_a_folder_shows_components():
    assert mode_for(_entity()) == MODE_CONTENTS


def test_one_file_is_not_a_list():
    """Pass 8 already turns a bucket of one into the file itself."""
    entity = _entity(removable_file_paths=["C:/d/only.zip"])
    assert mode_for(entity) == MODE_NONE


def test_something_with_no_inside_gets_no_section():
    """"Steam contains Steam" is what the redesign set out to remove."""
    assert mode_for(_entity(file_count=1, folder_count=0)) == MODE_NONE
    assert mode_for(_entity(file_count=0, folder_count=0)) == MODE_NONE


# ── the free summary, before any disk is touched ──────────────────

def test_the_first_answer_needs_no_disk():
    entity = _entity(children_sample=["checkpoints", "loras", "vae"])
    summary = quick_summary(entity)
    assert summary.provisional is True
    assert [r.label for r in summary.rows] == ["checkpoints", "loras", "vae"]


def test_the_free_summary_claims_no_sizes():
    """children_sample is scandir order without sizes — a sample, not a
    breakdown. For Steam it leads with ".cef-dev-tools-size.vdf"."""
    summary = quick_summary(_entity(children_sample=["a", "b"]))
    assert all(row.size_bytes == 0 for row in summary.rows)


# ── the measured breakdown ────────────────────────────────────────

def _tree(root):
    (root / "steamapps" / "common" / "Game A").mkdir(parents=True)
    (root / "steamapps" / "common" / "Game A" / "data.pak").write_bytes(b"x" * 900)
    (root / "steamapps" / "workshop").mkdir(parents=True)
    (root / "steamapps" / "workshop" / "mod.bin").write_bytes(b"x" * 300)
    (root / "logs").mkdir()
    (root / "logs" / "run.log").write_bytes(b"x" * 40)
    (root / "readme.txt").write_bytes(b"x" * 10)
    return root


def test_rules_turn_folders_into_concepts(tmp_path):
    contents = walk_contents(str(_tree(tmp_path)))
    named = {r.label: r.size_bytes for r in contents.rows if r.named}
    assert named.get("Installed games") == 900
    assert named.get("Workshop content") == 300


def test_unnamed_folders_still_appear(tmp_path):
    contents = walk_contents(str(_tree(tmp_path)))
    labels = [r.label for r in contents.rows]
    assert "logs" in labels, labels


def test_named_concepts_come_first(tmp_path):
    contents = walk_contents(str(_tree(tmp_path)))
    assert contents.rows[0].named is True


def test_the_total_is_everything_it_measured(tmp_path):
    contents = walk_contents(str(_tree(tmp_path)))
    assert contents.total_bytes == 900 + 300 + 40 + 10
    assert contents.total_files == 4


def test_a_rule_matches_relative_to_the_entity(tmp_path):
    """The same folder under a different app must not be mislabelled."""
    assert rule_for("steamapps/common/Half-Life")[1] == "Installed games"
    assert rule_for("something/else")[1] == ""


def test_the_tail_is_rolled_into_one_row(tmp_path):
    root = tmp_path / "many"
    root.mkdir()
    (root / "big").mkdir()
    (root / "big" / "f").write_bytes(b"x" * 10_000)
    for i in range(12):
        sub = root / f"tiny{i}"
        sub.mkdir()
        (sub / "f").write_bytes(b"x")
    contents = walk_contents(str(root))
    others = [r for r in contents.rows if r.is_other]
    assert len(others) == 1
    assert len(contents.rows) <= 7


def test_a_budget_that_runs_out_says_so(tmp_path):
    """A short answer that admits it is short beats a confident wrong one."""
    _tree(tmp_path)
    contents = walk_contents(str(tmp_path), budget_ms=-1)
    assert contents.truncated is True


def test_a_walk_can_be_stopped(tmp_path):
    _tree(tmp_path)
    contents = walk_contents(str(tmp_path), should_stop=lambda: True)
    assert contents.truncated is True


def test_a_missing_folder_is_not_an_error(tmp_path):
    assert not walk_contents(str(tmp_path / "gone"))


# ── file lists ────────────────────────────────────────────────────

def test_files_are_measured_biggest_first(tmp_path):
    small = tmp_path / "small.zip"
    small.write_bytes(b"x" * 10)
    big = tmp_path / "big.zip"
    big.write_bytes(b"x" * 5_000)
    contents = measure_files([str(small), str(big)])
    assert [r.label for r in contents.rows] == ["big.zip", "small.zip"]
    assert contents.total_bytes == 5_010


def test_a_file_that_is_gone_does_not_break_the_list(tmp_path):
    present = tmp_path / "here.zip"
    present.write_bytes(b"x" * 4)
    contents = measure_files([str(present), str(tmp_path / "gone.zip")])
    assert len(contents.rows) == 2


# ── the consequence line only speaks when it knows something ────

def test_it_says_nothing_the_contents_table_already_shows():
    """It used to restate the table verbatim underneath it.

    "Removing this deletes 29 files in 8 folders, including checkpoints
    (54.3 GB), LM (33.3 GB), loras (2.8 GB)" under a table saying exactly that
    adds no information and teaches the reader to skip the line that might one
    day matter.
    """
    contents = Contents(mode=MODE_CONTENTS, total_files=29, rows=[
        ContentRow(label="checkpoints", size_bytes=54 * 1024 ** 3,
                   path="checkpoints"),
        ContentRow(label="LM", size_bytes=33 * 1024 ** 3, path="LM"),
    ])
    assert removal_consequence(_entity(folder_count=8), contents) == ""


def test_a_file_selection_says_the_folder_survives():
    """Not visible in a list of file names, and worth knowing."""
    contents = Contents(mode=MODE_FILES, rows=[ContentRow(label="a.zip")])
    sentence = removal_consequence(_entity(), contents)
    assert "folder" in sentence


def test_cloud_sync_is_always_worth_saying():
    """It leaves the machine — nothing in the contents table can show that."""
    contents = Contents(mode=MODE_CONTENTS, total_files=4,
                        rows=[ContentRow(label="Docs", size_bytes=99)])
    sentence = removal_consequence(
        _entity(cloud_sync_provider="OneDrive"), contents)
    assert "OneDrive" in sentence and "device" in sentence


def test_partial_coverage_is_not_a_sentence_here():
    """It is a PARTIAL marker on the section header instead.

    "The scan stopped measuring before it reached the end" is Podbye talking
    about its own internals, and it made the size beside it read as unreliable.
    """
    contents = Contents(mode=MODE_CONTENTS, total_files=4, truncated=True,
                        rows=[ContentRow(label="sub", size_bytes=99)])
    assert removal_consequence(_entity(), contents) == ""


def test_with_no_contents_section_the_scale_is_the_consequence():
    """Nothing above it to duplicate, so the counts are worth stating."""
    contents = Contents(mode=MODE_NONE, rows=[])
    sentence = removal_consequence(_entity(file_count=12, folder_count=2),
                                   contents)
    assert "12" in sentence


def test_nothing_to_say_about_nothing():
    contents = Contents(mode=MODE_NONE, rows=[])
    assert removal_consequence(
        _entity(file_count=0, folder_count=0), contents) == ""


def test_there_is_always_a_sentence_even_with_no_ai():
    """Bulk AI is off by default, so anything vital cannot live inside it."""
    from app.config.settings_store import _DEFAULTS
    assert _DEFAULTS["ai_findings_enabled"] is False
    contents = Contents(mode=MODE_FILES, rows=[ContentRow(label="a.zip")])
    assert removal_consequence(_entity(), contents)


# ── the list is normalised before anything counts it ──────────

def test_a_repeated_path_is_listed_once():
    """Counted twice, shown twice, attempted twice — pick none of those."""
    from app.models.entity_contents import file_paths_of
    entity = _entity(removable_file_paths=["C:/x/a.zip", "C:/X/A.ZIP",
                                           "C:/x/b.zip"])
    assert len(file_paths_of(entity)) == 2


def test_a_blank_path_is_not_offered_for_deletion():
    from app.models.entity_contents import file_paths_of
    entity = _entity(removable_file_paths=["C:/x/a.zip", "", "   ", None])
    assert file_paths_of(entity) == ["C:/x/a.zip"]
