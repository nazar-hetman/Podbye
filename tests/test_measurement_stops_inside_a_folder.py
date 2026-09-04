"""The walk must honour its budget and Stop inside one big directory.

The budget and cancel checks used to run only between directories. A folder
holding hundreds of thousands of files was therefore measured to the end
whatever the budget said, and Stop did nothing until it finished - the one
shape where those controls matter most.

Everything here is built in tmp_path. No real user folder is read.
"""
import os

import pytest

from app.models.entity_contents import (
    _BUDGET_CHECK_EVERY, MODE_CONTENTS, walk_contents,
)


# Comfortably more than one check interval, small enough to build instantly.
MANY = _BUDGET_CHECK_EVERY * 3


@pytest.fixture
def flat(tmp_path):
    """One directory, many files - the shape the old code could not interrupt."""
    d = tmp_path / "flat"
    d.mkdir()
    for i in range(MANY):
        (d / f"f{i:05d}.bin").write_bytes(b"xyz")
    return tmp_path


def test_everything_is_measured_when_nothing_interrupts(flat):
    """The semantics that must not change."""
    c = walk_contents(str(flat), budget_ms=120000)
    assert c.total_files == MANY
    assert c.total_bytes == MANY * 3
    assert not c.truncated
    assert not c.cancelled
    assert c.mode == MODE_CONTENTS


def test_an_expired_budget_stops_inside_the_directory(flat):
    """Before the fix this returned every file, budget or not."""
    c = walk_contents(str(flat), budget_ms=0)
    assert c.truncated is True
    assert c.total_files < MANY, "the whole directory was measured anyway"
    assert not c.cancelled, "a budget is not a cancellation"


def test_a_stop_request_is_honoured_inside_the_directory(flat):
    c = walk_contents(str(flat), budget_ms=120000, should_stop=lambda: True)
    assert c.cancelled is True
    assert c.truncated is True
    assert c.total_files < MANY


def test_stopping_part_way_keeps_what_was_measured(flat):
    """A short answer that admits it is short, not an empty one."""
    seen = {"n": 0}

    def stop_after_two_checks():
        seen["n"] += 1
        return seen["n"] > 2

    c = walk_contents(str(flat), budget_ms=120000,
                      should_stop=stop_after_two_checks)
    assert c.cancelled is True
    assert 0 < c.total_files < MANY
    assert c.total_bytes == c.total_files * 3, "bytes and files disagree"


def test_the_check_does_not_fire_on_every_file(flat):
    """It is on the hot path, so it must stay a periodic check.

    should_stop is called once per interval, not once per file - the whole
    reason the interval exists.
    """
    calls = {"n": 0}

    def counting_stop():
        calls["n"] += 1
        return False

    c = walk_contents(str(flat), budget_ms=120000, should_stop=counting_stop)
    assert c.total_files == MANY
    # one per directory plus one per interval, nowhere near one per file
    assert calls["n"] <= (MANY // _BUDGET_CHECK_EVERY) + 4, calls["n"]
    assert calls["n"] < MANY / 10


def test_a_nested_tree_still_stops_between_directories(tmp_path):
    """The original between-directory check must still work."""
    for i in range(5):
        d = tmp_path / f"sub{i}"
        d.mkdir()
        (d / "a.bin").write_bytes(b"z")
    c = walk_contents(str(tmp_path), budget_ms=0)
    assert c.truncated is True


def test_an_empty_folder_is_not_reported_as_truncated(tmp_path):
    c = walk_contents(str(tmp_path), budget_ms=120000)
    assert c.total_files == 0
    assert not c.truncated
    assert not c.cancelled
