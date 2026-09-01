"""Opening a big session must not block the caller on a full JSON parse.

Reported: after scanning C:/, restarting, and pressing "Open Findings", the app
froze. Cause: last_run.json from that scan is 1.7 GB — ~1.6M raw findings — and
the open path called json.load() on it and then rebuilt every Finding object,
on the UI thread. Files in that format are already on users' disks, so the fix
has to make *reading* them cheap, not just stop writing them.

Findings renders entities, so the raw array is skipped at the byte level.
"""
import pytest

from app.config.settings_store import SettingsStore
from app.state import session_store
from app.state.scan_state import ScanState


@pytest.fixture
def sessions_dir(tmp_path, monkeypatch):
    d = tmp_path / "sessions"
    monkeypatch.setattr(session_store, "_sessions_dir", lambda: d)
    return d


def _finding(i):
    return {
        "category": "Cache & Temp", "semantic_label": "", "owner_confidence": "none",
        "path": f"C:/Users\\ExampleUser\\AppData\\Local\\Temp\\f{i}.tmp",
        "name": f"f{i}.tmp", "is_dir": False, "size": "1 KB", "size_bytes": 1024,
        "cloud_only": False, "reclaimable_bytes": 1024, "age": "3d",
        "modified": 1700000000.0, "risk": "Safe", "source_rule": "temp",
        "risk_reason": "temp file", "why": "What it is: Temp.",
        "last_access": "2026-07-01", "first_seen": "2026-07-01",
        "recommendation": "Remove", "ai_status": "none", "ai_explanation": "",
        "ai_error": "", "ai_model": "", "ai_language": "",
    }


def _entity(i):
    return {
        "path": f"C:/Users\\ExampleUser\\AppData\\Local\\App{i}", "name": f"App{i}",
        "entity_type": "application_data", "size_bytes": 5_000_000_000,
        "file_count": 120, "folder_count": 4, "risk": "Review",
        "is_entity": True, "category": "Application Data",
    }


def _legacy_snapshot(n_findings, n_entities=3):
    """A session in the pre-fix format: every raw finding serialized."""
    return {
        "session_id": "big", "target": "C:/", "scan_mode": "smart",
        "status": "completed", "start_time": 0.0, "last_update": 1.0,
        "scanned_count": n_findings, "total_size": 402_686_469_661,
        "category_totals": {"Application Data": {"count": 3, "size_bytes": 15_000_000_000}},
        "risk_totals": {"Review": 3},
        "findings": [_finding(i) for i in range(n_findings)],
        "findings_omitted": False,
        "entities": [_entity(i) for i in range(n_entities)],
        "scan_frontier": [],
    }


def _write(path, data):
    session_store._write_json_atomic(path, data)
    return path


# ── the skipping reader ───────────────────────────────────────────

def test_legacy_session_loads_without_its_findings(sessions_dir):
    """The 1.7 GB shape, scaled down: entities survive, findings are dropped."""
    path = _write(session_store._last_run_path(), _legacy_snapshot(5000))
    data = session_store._read_skipping_findings(path)
    assert data["findings"] == []
    assert len(data["entities"]) == 3
    # Everything the screens actually read must survive intact.
    assert data["session_id"] == "big"
    assert data["target"] == "C:/"
    assert data["scanned_count"] == 5000
    assert data["total_size"] == 402_686_469_661
    assert data["risk_totals"] == {"Review": 3}
    assert data["category_totals"]["Application Data"]["size_bytes"] == 15_000_000_000


def test_memory_does_not_grow_with_the_number_of_findings(sessions_dir, monkeypatch):
    """The property that makes a 1.7 GB file openable at all.

    json.load() holds every finding at once, which is why the old path needed
    gigabytes. This reader must stay flat: skipped findings cost nothing, so a
    file with 8x the findings costs the same to read. The read chunk is shrunk
    so both fixtures are many chunks long without writing 100 MB of test data.
    """
    import tracemalloc
    monkeypatch.setattr(session_store, "_READ_CHUNK", 64 * 1024)

    def peak_for(n):
        path = _write(sessions_dir / f"s{n}.json", _legacy_snapshot(n))
        tracemalloc.start()
        assert session_store._read_skipping_findings(path) is not None
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        return peak, path.stat().st_size

    small_peak, small_size = peak_for(5_000)
    big_peak, big_size = peak_for(40_000)
    assert big_size > small_size * 4, "the bigger file was not actually bigger"
    assert big_peak < small_peak * 1.5, (
        f"memory scaled with the findings: {small_peak} -> {big_peak}")


def test_empty_findings_array_still_reads_the_rest_of_the_file(sessions_dir):
    """json.dump writes an empty list inline as "[]" — no block to skip.

    Scanning on for a closing bracket here would run into "scan_frontier" and
    silently truncate the document.
    """
    snap = _legacy_snapshot(0, n_entities=2)
    snap["scan_frontier"] = ["C:/a", "C:/b"]
    path = _write(session_store._last_run_path(), snap)
    data = session_store._read_skipping_findings(path)
    assert data["findings"] == []
    assert len(data["entities"]) == 2
    assert data["scan_frontier"] == ["C:/a", "C:/b"]


def test_unexpected_layout_is_reported_not_guessed(sessions_dir):
    path = sessions_dir / "junk.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"session_id": "x", "entities": []}', encoding="utf-8")
    assert session_store._read_skipping_findings(path) is None


# ── the public loaders ────────────────────────────────────────────

def test_small_session_keeps_its_findings(sessions_dir):
    """Below the threshold nothing changes — a small scan still round-trips."""
    session_store.save_session(_legacy_snapshot(5))
    data = session_store.load_session()
    assert len(data["findings"]) == 5
    assert not data.get("findings_omitted")


def test_large_session_is_flagged_as_missing_its_findings(sessions_dir, monkeypatch):
    monkeypatch.setattr(session_store, "_SKIP_FINDINGS_ABOVE_BYTES", 1024)
    session_store.save_session(_legacy_snapshot(200))
    data = session_store.load_session()
    assert data["findings"] == []
    assert data["findings_omitted"] is True
    assert len(data["entities"]) == 3


def test_load_session_by_id_skips_findings_too(sessions_dir, monkeypatch):
    """History opens sessions by id — same file, same freeze."""
    monkeypatch.setattr(session_store, "_SKIP_FINDINGS_ABOVE_BYTES", 1024)
    session_store.append_to_history(_legacy_snapshot(200))
    data = session_store.load_session_by_id("big")
    assert data["findings"] == []
    assert len(data["entities"]) == 3


# ── what the restored state reports ───────────────────────────────

def test_restore_reports_real_totals_without_findings(sessions_dir):
    """A session with no raw findings must not report an empty, 0-byte scan."""
    st = ScanState()
    st.set_settings_store(SettingsStore())
    st.set_scan_mode("smart")
    snap = _legacy_snapshot(0)
    snap["scanned_count"] = 1_664_305
    snap["findings_omitted"] = True
    st.restore_from_session(snap)

    assert st.entity_count == 3
    assert st.total_size == 402_686_469_661
    assert st.total_size_str != "0 B"
    assert st.category_summary(), "no categories to show"
    assert st.risk_summary(), "no risk breakdown to show"
