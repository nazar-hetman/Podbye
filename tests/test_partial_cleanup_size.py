"""A bucket that was half cleaned has to report half its bytes.

remove_entities_by_path already shrank the file list and the count on a
partial cleanup, but left size_bytes alone — so a row whose files had just
been recycled still claimed all of its original size, in its own row, in its
category total and in the donut, until the next scan.
"""
import os

import pytest

from app.models.smart_entity import SmartEntity
from app.state.scan_state import ScanState, _measure_paths


@pytest.fixture
def bucket(tmp_path):
    paths = []
    for i in range(10):
        f = tmp_path / f"f{i}.bin"
        f.write_bytes(b"x" * 1_000_000)
        paths.append(str(f))
    entity = SmartEntity(path=str(tmp_path), name="bucket",
                         entity_type="cache_folder", size_bytes=10_000_000,
                         file_count=10, folder_count=0)
    entity.removable_file_paths = list(paths)
    state = ScanState()
    state._entities = [entity]
    return state, entity, paths


def test_a_half_cleaned_bucket_reports_half_its_bytes(bucket):
    state, entity, paths = bucket
    for p in paths[:5]:
        os.remove(p)
    state.remove_entities_by_path(set(paths[:5]))
    survivor = state._entities[0]
    assert survivor.file_count == 5
    assert survivor.size_bytes == 5_000_000


def test_a_fully_cleaned_bucket_goes_away(bucket):
    state, entity, paths = bucket
    for p in paths:
        os.remove(p)
    state.remove_entities_by_path(set(paths))
    assert state._entities == []


def test_an_untouched_bucket_is_not_re_measured(bucket):
    """Nothing of this entity was cleaned, so nothing about it should move —
    including a size that a live re-measure might disagree with."""
    state, entity, paths = bucket
    state.remove_entities_by_path({"C:/somewhere/else.bin"})
    assert state._entities[0].size_bytes == 10_000_000
    assert state._entities[0].file_count == 10


def test_reclaimable_never_exceeds_what_is_left(bucket):
    state, entity, paths = bucket
    entity.reclaimable_bytes = 10_000_000
    for p in paths[:8]:
        os.remove(p)
    state.remove_entities_by_path(set(paths[:8]))
    survivor = state._entities[0]
    assert survivor.reclaimable_bytes <= survivor.size_bytes


def test_a_missing_file_counts_as_nothing(tmp_path):
    real = tmp_path / "a.bin"
    real.write_bytes(b"x" * 100)
    assert _measure_paths([str(real), str(tmp_path / "gone.bin")]) == 100


def test_measuring_is_declined_rather_than_guessed_when_huge():
    """None means "no better answer than the stored one". A guess would be
    worse than a stale figure — this number drives the donut."""
    assert _measure_paths([f"C:/x/f{i}" for i in range(20)], limit=10) is None
