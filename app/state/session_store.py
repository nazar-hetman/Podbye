"""Session persistence — save/load/clear last_run.json and history.

Stores partial and complete analysis sessions so the app can resume
an interrupted run after restart.

Paths:
  %APPDATA%\\Vigil\\sessions\\last_run.json     — current/last session (resume)
  %APPDATA%\\Vigil\\sessions\\history.json      — lightweight history index
  %APPDATA%\\Vigil\\sessions\\session_{id}.json — full session data per entry
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

MAX_ANALYZE_HISTORY = 10
MAX_CLEANUP_HISTORY = 10


def _write_json_atomic(path: Path, data: Any) -> None:
    """Serialize *data* to *path* so readers only ever see a complete file.

    Sessions are written from daemon threads and can be several hundred MB. A
    plain ``open(path, "w")`` truncates the previous file up front, so a crash,
    a power loss, or interpreter shutdown killing the daemon mid-``json.dump``
    leaves behind a half-written file. Every loader treats a JSONDecodeError as
    "no session", so that silently discards the user's resumable scan.

    Writing to a temp file in the same directory and then ``os.replace``-ing it
    over the target makes the swap atomic: the old session survives a failed
    write intact, and a partial write only ever orphans the temp file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=str(path.parent),
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as tmp:
            tmp_name = tmp.name
            json.dump(data, tmp, indent=2, default=str)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_name, path)
        tmp_name = None
    finally:
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def _sessions_dir() -> Path:
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        return Path(appdata) / "Vigil" / "sessions"
    return Path.home() / ".config" / "vigil" / "sessions"


def _last_run_path() -> Path:
    return _sessions_dir() / "last_run.json"


def _history_path() -> Path:
    return _sessions_dir() / "history.json"


def _summary_path() -> Path:
    return _sessions_dir() / "summary.json"


def _last_run_summary_path() -> Path:
    return _sessions_dir() / "last_run_summary.json"


def _session_file_path(session_id: str) -> Path:
    return _sessions_dir() / f"session_{session_id}.json"


def _cleanup_record_path(timestamp: int) -> Path:
    return _sessions_dir() / f"cleanup_{timestamp}.json"


# ── Public API ───────────────────────────────────────────────────

def save_session(data: dict) -> bool:
    """Write session data to last_run.json. Returns True on success."""
    path = _last_run_path()
    try:
        data["saved_at"] = time.time()
        _write_json_atomic(path, data)
        _save_last_run_summary(_build_last_run_summary(data))
        return True
    except OSError:
        return False


def load_session() -> dict | None:
    """Load last_run.json. Returns dict or None if missing/corrupt."""
    path = _last_run_path()
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return None


def load_session_summary() -> dict | None:
    """Load lightweight last-run metadata without parsing large findings arrays."""
    summary_path = _last_run_summary_path()
    if summary_path.exists():
        try:
            with open(summary_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError):
            pass

    data = _load_session_prefix_summary(_last_run_path())
    if data:
        try:
            _save_last_run_summary(data)
        except OSError:
            pass
    return data


def clear_session() -> bool:
    """Delete last_run.json. Returns True on success."""
    path = _last_run_path()
    summary_path = _last_run_summary_path()
    try:
        if path.exists():
            path.unlink()
        if summary_path.exists():
            summary_path.unlink()
        return True
    except OSError:
        return False


def has_unfinished_session() -> bool:
    """Check if an unfinished (stopped/crashed) session exists."""
    data = load_session_summary()
    if not data:
        return False
    return data.get("status") in ("stopped", "running")


def _default_summary() -> dict:
    return {
        "total_recovered_bytes": 0,
        "cleanup_sessions": 0,
        "total_cleanup_items": 0,
        "total_scanned_bytes": 0,
        "analyze_sessions": 0,
        "total_analyzed_items": 0,
        "updated_at": 0.0,
    }


def _sum_reclaimable(items: list[dict]) -> int:
    total = 0
    for item in items or []:
        value = item.get("reclaimable_bytes", 0)
        if isinstance(value, (int, float)):
            total += int(value)
    return total


def load_summary() -> dict:
    """Load cumulative workstation summary stats."""
    path = _summary_path()
    if not path.exists():
        return _default_summary()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            summary = _default_summary()
            summary.update(data)
            return summary
    except (json.JSONDecodeError, OSError):
        pass
    return _default_summary()


def _save_summary(summary: dict) -> None:
    summary["updated_at"] = time.time()
    _write_json_atomic(_summary_path(), summary)


# ── Snapshot builder ─────────────────────────────────────────────

def _build_history_record(data: dict) -> dict:
    """Extract a lightweight summary record for the history index."""
    risk_totals = data.get("risk_totals", {})
    entities = data.get("entities", [])
    findings = data.get("findings", [])
    entity_count = len(entities)
    finding_count = len(findings)
    display_count = entity_count if entity_count > 0 else finding_count
    reclaimable_bytes = data.get("total_reclaimable_bytes")
    if not isinstance(reclaimable_bytes, (int, float)):
        reclaimable_bytes = _sum_reclaimable(entities or findings)
    return {
        "session_id": data.get("session_id", ""),
        "target": data.get("target", ""),
        "scan_mode": data.get("scan_mode", "smart"),
        "status": data.get("status", ""),
        "start_time": data.get("start_time", 0.0),
        "saved_at": data.get("saved_at", time.time()),
        "scanned_count": data.get("scanned_count", 0),
        "display_count": display_count,
        "total_size": data.get("total_size", 0),
        "total_reclaimable_bytes": int(reclaimable_bytes or 0),
        "risk_totals": risk_totals,
        "category_totals": data.get("category_totals", {}),
    }


def _build_last_run_summary(data: dict) -> dict:
    """Extract lightweight metadata for startup/Home rendering."""
    summary = _build_history_record(data)
    entities = data.get("entities", [])
    findings = data.get("findings", [])
    display_items = entities if entities else findings
    risk_totals = data.get("risk_totals", {}) or {}
    display_count = len(display_items)
    if display_count == 0 and isinstance(risk_totals, dict):
        display_count = sum(
            int(v or 0) for v in risk_totals.values()
            if isinstance(v, (int, float))
        )
    display_unit = "entities" if entities else ("files" if findings else "items")
    ai_ready_count = sum(1 for it in display_items if it.get("ai_status") in ("ready", "done"))
    ai_total_count = len(display_items) if display_items else display_count
    summary.update({
        "last_update": data.get("last_update", 0.0),
        "saved_at": data.get("saved_at", time.time()),
        "has_entities": bool(entities),
        "display_count": display_count,
        "display_unit": display_unit,
        "ai_ready_count": ai_ready_count,
        "ai_total_count": ai_total_count,
    })
    return summary


def _save_last_run_summary(summary: dict) -> None:
    _write_json_atomic(_last_run_summary_path(), summary)


def _load_session_prefix_summary(path: Path) -> dict | None:
    """Recover top-level session metadata without loading giant arrays into memory."""
    if not path.exists():
        return None
    try:
        marker = '"findings":'
        marker_alt = '"entities":'
        buf = ""
        with open(path, "r", encoding="utf-8") as f:
            while len(buf) < 2_000_000:
                chunk = f.read(65_536)
                if not chunk:
                    break
                buf += chunk
                idx = buf.find(marker)
                if idx == -1:
                    idx = buf.find(marker_alt)
                if idx != -1:
                    prefix = buf[:idx]
                    lightweight_json = prefix.rstrip()
                    if not lightweight_json.endswith(","):
                        lightweight_json = lightweight_json.rstrip() + ","
                    lightweight_json += '\n  "findings": [],\n  "entities": []\n}'
                    data = json.loads(lightweight_json)
                    if isinstance(data, dict):
                        summary = _build_last_run_summary(data)
                        if summary.get("status") == "completed":
                            summary["ai_ready_count"] = summary.get("display_count", 0)
                            summary["ai_total_count"] = summary.get("display_count", 0)
                        return summary
                    return None

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return _build_last_run_summary(data)
    except (json.JSONDecodeError, OSError, ValueError):
        pass
    return None


def append_to_history(data: dict) -> bool:
    """Append a completed session to the history index and save its full data."""
    try:
        sess_dir = _sessions_dir()
        sess_dir.mkdir(parents=True, exist_ok=True)
        session_id = data.get("session_id", "")
        if session_id:
            _write_json_atomic(_session_file_path(session_id), data)
        history = load_history()
        history = [r for r in history if r.get("session_id") != session_id]
        history.insert(0, _build_history_record(data))
        while len(history) > MAX_ANALYZE_HISTORY:
            old = history.pop()
            old_path = _session_file_path(old.get("session_id", ""))
            try:
                if old_path.exists():
                    old_path.unlink()
            except OSError:
                pass
        _write_json_atomic(_history_path(), history)
        summary = load_summary()
        summary["analyze_sessions"] += 1
        summary["total_scanned_bytes"] += int(data.get("total_size", 0) or 0)
        analyzed_count = len(data.get("entities", [])) or len(data.get("findings", [])) or int(data.get("scanned_count", 0) or 0)
        summary["total_analyzed_items"] += int(analyzed_count)
        _save_summary(summary)
        return True
    except OSError:
        return False


def load_history() -> list:
    """Load history index (newest first). Returns empty list if missing/corrupt."""
    path = _history_path()
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return []


def save_cleanup_record(session_id: str, items: list, result, mode: str) -> bool:
    """Write a cleanup operation record to cleanup_{timestamp}.json.

    Args:
        session_id: ID of the scan session that spawned the cleanup.
        items: list of dicts with path/name/size/risk/category fields.
        result: CleanupResult from cleanup_engine.
        mode: "recycle_bin" or "permanent".
    """
    ts = int(time.time())
    data = {
        "type": "cleanup",
        "timestamp": ts,
        "session_id": session_id,
        "mode": mode,
        "total_bytes_freed": result.total_bytes_freed,
        "succeeded_count": len(result.succeeded),
        "in_use_count": len(getattr(result, "in_use", [])),
        "failed_count": len(result.failed),
        "skipped_protected_count": len(result.skipped_protected),
        "result_state": (
            "failed" if len(result.failed) > 0 else
            "partial" if len(getattr(result, "in_use", [])) > 0 and len(result.succeeded) > 0 else
            "in_use" if len(getattr(result, "in_use", [])) > 0 else
            "success" if len(result.succeeded) > 0 else
            "already_clean"
        ),
        "items": items,
        "errors_by_path": result.errors_by_path,
    }
    try:
        _write_json_atomic(_cleanup_record_path(ts), data)
        records = _load_all_cleanup_records()
        for extra in records[MAX_CLEANUP_HISTORY:]:
            extra_path = _cleanup_record_path(int(extra.get("timestamp", 0) or 0))
            try:
                if extra_path.exists():
                    extra_path.unlink()
            except OSError:
                pass
        summary = load_summary()
        summary["cleanup_sessions"] += 1
        summary["total_recovered_bytes"] += int(result.total_bytes_freed or 0)
        summary["total_cleanup_items"] += int(len(result.succeeded or []))
        _save_summary(summary)
        return True
    except OSError:
        return False


def _load_all_cleanup_records() -> list[dict]:
    """Return all cleanup records, newest first."""
    sess_dir = _sessions_dir()
    if not sess_dir.exists():
        return []
    records = []
    for p in sess_dir.glob("cleanup_*.json"):
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("type") == "cleanup":
                records.append(data)
        except (json.JSONDecodeError, OSError):
            pass
    records.sort(key=lambda r: r.get("timestamp", 0), reverse=True)
    return records


def load_cleanup_records() -> list[dict]:
    """Return retained cleanup records, newest first."""
    records = _load_all_cleanup_records()
    return records[:MAX_CLEANUP_HISTORY]


def load_session_by_id(session_id: str) -> dict | None:
    """Load full session data by id. Returns None if missing/corrupt."""
    path = _session_file_path(session_id)
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return None


def delete_session_from_history(session_id: str) -> bool:
    """Remove a session from the history index and delete its data file."""
    try:
        history = load_history()
        history = [r for r in history if r.get("session_id") != session_id]
        _write_json_atomic(_history_path(), history)
        full_path = _session_file_path(session_id)
        if full_path.exists():
            full_path.unlink()
        return True
    except OSError:
        return False


def build_snapshot(
    session_id: str,
    target: str,
    scan_mode: str,
    status: str,
    start_time: float,
    scanned_count: int,
    total_size: int,
    category_totals: dict,
    risk_totals: dict,
    findings_dicts: list[dict],
    entities_dicts: list[dict] | None = None,
    scan_frontier: list[str] | None = None,
    findings_omitted: bool = False,
) -> dict:
    """Build a serializable session snapshot dict.

    ``scan_frontier`` is the list of directories the scanner had discovered but
    not yet walked when the snapshot was taken. On resume it lets the scanner
    continue from where it stopped instead of re-walking the whole tree. Empty
    for a completed scan.
    """
    return {
        "session_id": session_id,
        "target": target,
        "scan_mode": scan_mode,
        "status": status,
        "start_time": start_time,
        "last_update": time.time(),
        "scanned_count": scanned_count,
        "total_size": total_size,
        "category_totals": category_totals,
        "risk_totals": risk_totals,
        "findings": findings_dicts,
        # True when the raw per-file list was too large to persist. Findings
        # renders entities, so this only tells a resume it cannot dedup by path.
        "findings_omitted": findings_omitted,
        "entities": entities_dicts or [],
        "scan_frontier": scan_frontier or [],
    }
