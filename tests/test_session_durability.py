"""Session persistence durability — atomic writes and save-thread joins.

Sessions are written from daemon threads while a scan mutates state on the main
thread. These tests pin the two failure modes that silently cost a user their
resumable scan: a partially-written file replacing a good one, and aggregates
being read while the scanner mutates them.
"""
import json
import threading
import time

import pytest

from app.state import session_store
from app.state.scan_state import ScanState
from app.models.finding import Finding


@pytest.fixture
def sessions_dir(tmp_path, monkeypatch):
    """Point the session store at a temp dir instead of %APPDATA%."""
    d = tmp_path / "sessions"
    monkeypatch.setattr(session_store, "_sessions_dir", lambda: d)
    return d


def _snapshot(session_id="abc", status="running", findings=None):
    return session_store.build_snapshot(
        session_id=session_id, target="C:/x", scan_mode="smart", status=status,
        start_time=0.0, scanned_count=0, total_size=0,
        category_totals={}, risk_totals={}, findings_dicts=findings or [],
    )


def test_save_session_roundtrips(sessions_dir):
    assert session_store.save_session(_snapshot(session_id="s1"))
    loaded = session_store.load_session()
    assert loaded["session_id"] == "s1"


def test_failed_write_leaves_previous_session_intact(sessions_dir, monkeypatch):
    """A crash mid-write must not destroy the session already on disk.

    The old truncate-in-place write left a half-written file that load_session
    rejects as corrupt, silently discarding a resumable scan.
    """
    session_store.save_session(_snapshot(session_id="good"))

    def boom(*args, **kwargs):
        raise RuntimeError("disk died mid-serialization")

    monkeypatch.setattr(session_store.json, "dump", boom)
    with pytest.raises(RuntimeError):
        session_store.save_session(_snapshot(session_id="doomed"))

    # The previous session must still be loadable and unchanged.
    loaded = session_store.load_session()
    assert loaded is not None, "failed write destroyed the existing session"
    assert loaded["session_id"] == "good"


def test_failed_write_leaves_no_stray_temp_files(sessions_dir, monkeypatch):
    session_store.save_session(_snapshot(session_id="good"))

    def boom(*args, **kwargs):
        raise RuntimeError("nope")

    monkeypatch.setattr(session_store.json, "dump", boom)
    with pytest.raises(RuntimeError):
        session_store.save_session(_snapshot(session_id="doomed"))

    assert [p.name for p in sessions_dir.iterdir() if p.name.endswith(".tmp")] == []


def test_written_session_is_always_complete_json(sessions_dir):
    """Readers never observe a torn file: the swap into place is atomic."""
    big = [{"path": f"C:/f{i}", "name": f"f{i}", "size_bytes": i} for i in range(500)]
    session_store.save_session(_snapshot(session_id="big", findings=big))
    raw = session_store._last_run_path().read_text(encoding="utf-8")
    assert len(json.loads(raw)["findings"]) == 500


# ── ScanState background save ────────────────────────────────────


def _finding(path, category="Cache"):
    return Finding(path=path, name=path.rsplit("/", 1)[-1], is_dir=False,
                   size_bytes=10, extension="", modified=0.0, accessed=0.0,
                   parent="C:/", category=category, risk="Safe")


def test_aggregates_snapshot_is_decoupled_from_live_state(sessions_dir):
    """The snapshot must be a copy, not a view of the mutating aggregates."""
    st = ScanState()
    st.add_findings([_finding("C:/a", "Cache")])
    snap = st._aggregates_snapshot()

    st.add_findings([_finding("C:/b", "Logs")])

    assert "Logs" not in snap["cat_totals"], "snapshot aliased live aggregates"
    assert snap["total_size"] == 10


def test_background_save_survives_concurrent_finding_ingest(sessions_dir):
    """Autosave must still land while the scanner adds findings.

    A smoke test for the save path under concurrent ingest, not a reproduction:
    the race it guards against (_build_snapshot iterating the live _cat_counts
    on the save thread while a new category arrives, raising "dictionary changed
    size during iteration" into a swallowing except) has too narrow a window to
    trigger on demand. Verified: this test still passes against the pre-fix
    code, so treat it as a regression net for the save path generally.
    """
    st = ScanState()
    st._session_id = "race"
    st.add_findings([_finding(f"C:/seed{i}") for i in range(50)])

    stop = threading.Event()

    def churn():
        i = 0
        while not stop.is_set() and i < 400:
            # Each add introduces a brand-new category key, maximizing the
            # chance of mutating the dict the saver would have been iterating.
            st.add_findings([_finding(f"C:/x{i}", f"Cat{i}")])
            i += 1
            time.sleep(0.001)

    t = threading.Thread(target=churn, daemon=True)
    t.start()
    try:
        for _ in range(5):
            st._save_session_background("running", lightweight=True)
            time.sleep(0.02)
        assert st.wait_for_saves(timeout=30.0), "background saves did not finish"
    finally:
        stop.set()
        t.join(timeout=5)

    loaded = session_store.load_session()
    assert loaded is not None, "concurrent ingest silently killed every autosave"
    assert loaded["session_id"] == "race"


def test_wait_for_saves_returns_true_when_idle(sessions_dir):
    assert ScanState().wait_for_saves(timeout=1.0) is True
