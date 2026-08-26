"""Retained sessions must not keep bytes the loader refuses to return.

Measured on the reporting machine (2026-08-12): 3.44 GB of sessions, of which
3.42 GB sat in three files written before Podbye stopped persisting raw
findings. sweep_orphaned_files could not touch them — history.json still names
all three, and deleting an indexed session is exactly what that sweep must
never do. Meanwhile _load_session_file discards the findings array of any file
past _SKIP_FINDINGS_ABOVE_BYTES, so those gigabytes were unreadable by
construction.

Compaction closes the gap: same threshold, so if the loader would throw the
findings away, they are not kept on disk.
"""
import json
import os
import time

import pytest

from app.state import session_store as ss

HOUR = 60 * 60


@pytest.fixture
def sessions(tmp_path, monkeypatch):
    """A sessions directory under a throwaway %APPDATA%, with a tiny threshold.

    The real threshold is 32 MB. Pushing that much through the temp directory
    on every run buys no coverage — what is under test is which files get
    picked, not how many bytes fit in a chunk.
    """
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(ss, "_SKIP_FINDINGS_ABOVE_BYTES", 4096)
    d = tmp_path / "Podbye" / "sessions"
    d.mkdir(parents=True)
    return d


def _age(path, seconds=2 * HOUR):
    old = time.time() - seconds
    os.utime(path, (old, old))
    return path


def _legacy_session(sessions, name="session_b7ff978b.json", findings=200,
                    entities=2, age_seconds=2 * HOUR):
    """Write a pre-fix session file: a fat findings array, written atomically.

    _write_json_atomic is what produced the real files, and its indent=2 output
    is what _read_skipping_findings keys off — hand-rolled JSON would not
    exercise the same path.
    """
    ss._write_json_atomic(sessions / name, {
        "session_id": name[len("session_"):-len(".json")],
        "target": "C:/",
        "scan_mode": "smart",
        "status": "completed",
        "start_time": 1.0,
        "scanned_count": findings,
        "total_size": 999,
        "category_totals": {},
        "risk_totals": {"Review": entities},
        "findings": [
            {"path": f"C:/f/{i}", "name": str(i), "size_bytes": i,
             "why": "What it is: Cache. Why flagged: temporary data."}
            for i in range(findings)
        ],
        "findings_omitted": False,
        "entities": [
            {"path": f"C:/e/{i}", "name": f"e{i}", "entity_type": "cache_folder",
             "size_bytes": 10, "risk": "Optional", "summary": "Cache · 1 files"}
            for i in range(entities)
        ],
        "scan_frontier": [],
    })
    return _age(sessions / name, age_seconds)


def _history(sessions, *session_ids):
    (sessions / "history.json").write_text(
        json.dumps([{"session_id": s} for s in session_ids]), encoding="utf-8")


# ── what must shrink ──────────────────────────────────────────────

def test_compacts_a_session_the_loader_already_reads_without_findings(sessions):
    path = _legacy_session(sessions)
    before = path.stat().st_size

    count, reclaimed = ss.compact_oversized_sessions()

    assert count == 1
    assert path.stat().st_size < before
    assert reclaimed == before - path.stat().st_size


def test_compaction_keeps_everything_the_session_can_still_show(sessions):
    """Findings were already discarded on read; entities are what Findings renders."""
    _history(sessions, "b7ff978b")
    _legacy_session(sessions, entities=3)

    ss.compact_oversized_sessions()
    data = ss.load_session_by_id("b7ff978b")

    assert data is not None
    assert len(data["entities"]) == 3
    assert data["target"] == "C:/"
    assert data["risk_totals"] == {"Review": 3}
    assert data["findings"] == []
    assert data["findings_omitted"] is True


def test_an_indexed_session_is_compacted_not_deleted(sessions):
    """The sweep must never take an indexed file; compaction is the other half."""
    _history(sessions, "b7ff978b")
    path = _legacy_session(sessions)

    ss.sweep_orphaned_files()
    assert path.exists(), "sweep deleted a session named by the index"

    ss.compact_oversized_sessions()
    assert path.exists()


def test_last_run_is_compacted_too(sessions):
    """The 2.8 GB file in the original report was a last_run.json."""
    path = _legacy_session(sessions, name="last_run.json")
    before = path.stat().st_size

    count, _ = ss.compact_oversized_sessions()

    assert count == 1
    assert path.stat().st_size < before
    assert ss.load_session()["findings"] == []


def test_reports_what_it_reclaimed(sessions):
    a = _legacy_session(sessions, name="session_aaaa.json")
    b = _legacy_session(sessions, name="session_bbbb.json")
    expected = (a.stat().st_size + b.stat().st_size)

    count, reclaimed = ss.compact_oversized_sessions()

    assert count == 2
    assert reclaimed == expected - (a.stat().st_size + b.stat().st_size)
    assert reclaimed > 0


# ── what must be left alone ───────────────────────────────────────

def test_leaves_files_under_the_threshold_untouched(sessions):
    """Below the threshold the loader returns the findings, so they must stay."""
    path = _legacy_session(sessions, findings=1, entities=1)
    assert path.stat().st_size <= ss._SKIP_FINDINGS_ABOVE_BYTES
    before = path.read_bytes()

    count, reclaimed = ss.compact_oversized_sessions()

    assert (count, reclaimed) == (0, 0)
    assert path.read_bytes() == before


def test_leaves_fresh_files_alone(sessions):
    """A concurrent instance may be checkpointing this scan right now."""
    path = _legacy_session(sessions, age_seconds=30)
    before = path.read_bytes()

    count, _ = ss.compact_oversized_sessions()

    assert count == 0
    assert path.read_bytes() == before


def test_a_save_during_the_read_wins(sessions):
    """Reading gigabytes takes seconds; a scan can checkpoint in that window."""
    path = _legacy_session(sessions, name="last_run.json")
    real_read = ss._read_skipping_findings

    def read_then_someone_saves(p):
        data = real_read(p)
        ss._write_json_atomic(p, {"session_id": "newer", "findings": [],
                                  "entities": [], "scan_frontier": []})
        return data

    ss._read_skipping_findings = read_then_someone_saves
    try:
        count, _ = ss.compact_oversized_sessions()
    finally:
        ss._read_skipping_findings = real_read

    assert count == 0
    assert ss.load_session()["session_id"] == "newer", "clobbered a newer save"


def test_never_rewrites_an_unparseable_file(sessions):
    """A full parse of a multi-gigabyte file is the freeze this path avoids."""
    path = sessions / "session_garbage.json"
    path.write_bytes(b"{not json" + b"x" * 8192)
    _age(path)
    before = path.read_bytes()

    count, _ = ss.compact_oversized_sessions()

    assert count == 0
    assert path.read_bytes() == before


@pytest.mark.parametrize("name", ["history.json", "summary.json",
                                  "cleanup_1784660224.json"])
def test_never_touches_the_index_or_cleanup_records(sessions, name):
    path = sessions / name
    path.write_text(json.dumps({"pad": "y" * 8192}), encoding="utf-8")
    _age(path)
    before = path.read_bytes()

    ss.compact_oversized_sessions()

    assert path.read_bytes() == before


def test_a_missing_sessions_folder_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "nothing-here"))
    assert ss.compact_oversized_sessions() == (0, 0)


def test_running_twice_changes_nothing_the_second_time(sessions):
    """Startup runs this every launch; it must settle, not churn."""
    _legacy_session(sessions)
    ss.compact_oversized_sessions()
    settled = (sessions / "session_b7ff978b.json").read_bytes()

    count, reclaimed = ss.compact_oversized_sessions()

    assert (count, reclaimed) == (0, 0)
    assert (sessions / "session_b7ff978b.json").read_bytes() == settled
