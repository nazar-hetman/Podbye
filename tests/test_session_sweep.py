"""Crash leftovers in the sessions folder must not accumulate forever.

Measured on the reporting machine (2026-08-01): a 6.9 GB sessions folder of
which 3.55 GB was unreachable — 2.80 GB in two atomic-write temp files left by
killed processes, and 839 MB in a session file whose history record was gone.
MAX_ANALYZE_HISTORY prunes by walking the index, so neither kind is ever seen.

Podbye now protects its own data folder from cleanup, so if this sweep does not
reclaim them, nothing will.
"""
import json
import time

import pytest

from app.state import session_store as ss

# Sizes are deliberately tiny. The reclaimed-bytes arithmetic is what is
# under test, and writing the real multi-megabyte shapes put tens of MB
# through the temp directory on every run for no extra coverage.
KB = 1024
HOUR = 60 * 60


@pytest.fixture
def sessions(tmp_path, monkeypatch):
    """A sessions directory under a throwaway %APPDATA%."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    d = tmp_path / "Podbye" / "sessions"
    d.mkdir(parents=True)
    return d


def _write(path, size=KB, age_seconds=2 * HOUR):
    path.write_bytes(b"x" * size)
    old = time.time() - age_seconds
    import os
    os.utime(path, (old, old))
    return path


def _history(sessions, *session_ids):
    (sessions / "history.json").write_text(
        json.dumps([{"session_id": s} for s in session_ids]), encoding="utf-8")


# ── what must go ──────────────────────────────────────────────────

def test_removes_atomic_write_temp_files(sessions):
    _history(sessions)
    _write(sessions / ".last_run.json._0cud1h0.tmp", 3 * KB)  # 2.8 GB in life
    _write(sessions / ".last_run_summary.json.z65fk_f0.tmp", 300)

    removed, reclaimed = ss.sweep_orphaned_files()

    assert removed == 2
    assert reclaimed > 0
    assert not list(sessions.glob("*.tmp"))


def test_removes_session_files_missing_from_history(sessions):
    """The index is what History reads — a file it does not name is unreachable."""
    _history(sessions, "b7ff978b")
    kept = _write(sessions / "session_b7ff978b.json", 2 * KB)
    orphan = _write(sessions / "session_7c918b87.json", 2 * KB)  # 839 MB in life

    removed, _ = ss.sweep_orphaned_files()

    assert removed == 1
    assert kept.exists()
    assert not orphan.exists()


def test_reports_what_it_reclaimed(sessions):
    _history(sessions)
    _write(sessions / ".last_run.json.abc.tmp", 5 * KB)
    _write(sessions / "session_dead.json", 3 * KB)

    removed, reclaimed = ss.sweep_orphaned_files()

    assert removed == 2
    assert reclaimed == 8 * KB


# ── what must stay ────────────────────────────────────────────────

def test_leaves_fresh_files_alone(sessions):
    """A concurrent instance may be writing right now."""
    _history(sessions)
    live_tmp = _write(sessions / ".last_run.json.live.tmp", 1 * KB, age_seconds=5)
    just_written = _write(sessions / "session_brandnew.json", 1 * KB, age_seconds=30)

    removed, _ = ss.sweep_orphaned_files()

    assert removed == 0
    assert live_tmp.exists()
    assert just_written.exists(), "raced append_to_history writing its index record"


@pytest.mark.parametrize("name", [
    "history.json", "summary.json", "last_run.json", "last_run_summary.json",
    "cleanup_1784660224.json",
])
def test_never_touches_index_or_cleanup_records(sessions, name):
    _history(sessions)
    kept = _write(sessions / name, 4096)

    ss.sweep_orphaned_files()

    assert kept.exists()


def test_an_unreadable_index_deletes_nothing(sessions):
    """A corrupt history.json is not evidence that every session is orphaned."""
    (sessions / "history.json").write_text("{not json", encoding="utf-8")
    survivor = _write(sessions / "session_b7ff978b.json", 2 * KB)

    removed, reclaimed = ss.sweep_orphaned_files()

    assert (removed, reclaimed) == (0, 0)
    assert survivor.exists()


def test_a_missing_sessions_folder_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "nothing-here"))
    assert ss.sweep_orphaned_files() == (0, 0)


def test_subdirectories_are_ignored(sessions):
    _history(sessions)
    d = sessions / "session_weird.json"      # a directory with a matching name
    d.mkdir()
    ss.sweep_orphaned_files()
    assert d.is_dir()


# ── the real shape ────────────────────────────────────────────────

def test_the_reported_folder_layout(sessions):
    """The exact mix measured on the reporting machine, in miniature."""
    _history(sessions, "60696cd0", "b7ff978b", "c0e6d9f0", "c4881149",
             "c78d9d90", "e4e5739c", "ea799ec9")
    for sid in ["60696cd0", "b7ff978b", "c0e6d9f0", "c4881149",
                "c78d9d90", "e4e5739c", "ea799ec9"]:
        _write(sessions / f"session_{sid}.json", KB)
    _write(sessions / "session_7c918b87.json", KB)            # orphan
    _write(sessions / ".last_run.json._0cud1h0.tmp", KB)      # orphan
    _write(sessions / ".last_run_summary.json.z65fk_f0.tmp", KB)  # orphan
    for ts in [1784660224, 1785406947]:
        _write(sessions / f"cleanup_{ts}.json", 4096)
    for n in ["last_run.json", "last_run_summary.json", "summary.json"]:
        _write(sessions / n, 4096)

    removed, reclaimed = ss.sweep_orphaned_files()

    assert removed == 3
    assert reclaimed == 3 * KB
    assert len(list(sessions.glob("session_*.json"))) == 7
    assert not list(sessions.glob("*.tmp"))


def test_a_missing_index_still_reclaims_temp_files(sessions):
    """Temp files are unambiguous garbage; session files wait for a good index."""
    survivor = _write(sessions / "session_b7ff978b.json", 2 * KB)
    junk = _write(sessions / ".last_run.json.abc.tmp", 1 * KB)

    removed, reclaimed = ss.sweep_orphaned_files()

    assert (removed, reclaimed) == (1, KB)
    assert survivor.exists(), "deleted a session because the index was missing"
    assert not junk.exists()


def test_a_malformed_index_does_not_orphan_everything(sessions):
    """history.json holding a dict, not a list — still not a licence to delete."""
    (sessions / "history.json").write_text('{"session_id": "x"}', encoding="utf-8")
    survivor = _write(sessions / "session_b7ff978b.json", 2 * KB)

    ss.sweep_orphaned_files()

    assert survivor.exists()
