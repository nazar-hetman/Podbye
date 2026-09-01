"""Session persistence — save/load/clear last_run.json and history.

Stores partial and complete analysis sessions so the app can resume
an interrupted run after restart.

Paths:
  %APPDATA%\\Podbye\\sessions\\last_run.json     — current/last session (resume)
  %APPDATA%\\Podbye\\sessions\\history.json      — lightweight history index
  %APPDATA%\\Podbye\\sessions\\session_{id}.json — full session data per entry
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

# Above this size a session is read with _read_skipping_findings() instead of
# json.load(). Sessions written before Podbye stopped persisting raw findings
# hold ~1.6M of them in one 1.7 GB array; parsing that allocates gigabytes and
# blocks for minutes. Anything under the threshold parses in well under a
# second, so the plain loader stays in charge of the common case.
_SKIP_FINDINGS_ABOVE_BYTES = 32 * 1024 * 1024

# Session files are always written by _write_json_atomic with indent=2, so a
# top-level array closes on a line of exactly two spaces + "]". JSON escapes
# newlines inside strings, so this byte sequence can never occur in data.
_TOP_LEVEL_ARRAY_END = b"\n  ]"
_FINDINGS_KEY = b'"findings":'
# The keys written before "findings" are all scalars/small dicts. If we have not
# found the key within this much of the file, the layout is not what we expect.
_MAX_FINDINGS_KEY_OFFSET = 8 * 1024 * 1024
_READ_CHUNK = 8 * 1024 * 1024


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
        return Path(appdata) / "Podbye" / "sessions"
    return Path.home() / ".config" / "podbye" / "sessions"


def sessions_dir() -> Path:
    """Public accessor for the session store location.

    Settings/About reports this path, and it must read the real value rather
    than restate a literal — About used to list two directories that no code
    had ever created.
    """
    return _sessions_dir()


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


# ── Crash-leftover sweep ─────────────────────────────────────────

# How long a stray file must sit untouched before the sweep will take it.
# Another Podbye instance may be writing right now; a live temp file is seconds
# old, and append_to_history writes session_<id>.json a moment before it adds
# the matching history record.
_STALE_AFTER_SECONDS = 60 * 60


def sweep_orphaned_files(now: float | None = None) -> tuple[int, int]:
    """Delete files in the sessions directory that nothing can ever reach.

    Returns ``(files_removed, bytes_reclaimed)``.

    Two kinds accumulate, neither covered by MAX_ANALYZE_HISTORY:

    * ``.<name>.<rand>.tmp`` — _write_json_atomic unlinks its temp file in a
      finally block, which does not run when the process is killed rather than
      raising. Measured on a real profile: 2.80 GB in two such files, one of
      them a 2.8 GB last_run.json left by a single kill.
    * ``session_<id>.json`` with no record in history.json — the index is what
      History reads and what pruning walks, so a file it does not name can
      never be opened, listed, or evicted. Measured: 839 MB in one file.

    Together that was 3.55 GB on a 6.9 GB sessions folder. Fresh files are
    always left alone, so a concurrent instance is never disturbed.

    Sessions the index *does* name are never deleted here — see
    compact_oversized_sessions() for the oversized ones.
    """
    now = time.time() if now is None else now
    sess_dir = _sessions_dir()
    if not sess_dir.is_dir():
        return 0, 0

    # None means "the index could not be trusted". load_history() reports a
    # corrupt or missing file as an empty list, which is indistinguishable from
    # a profile with no sessions — acting on that would delete every session
    # file the moment history.json got truncated. Temp files are unambiguous
    # garbage either way, so only the session sweep waits for a good index.
    known: set | None = None
    hist_path = _history_path()
    if hist_path.is_file():
        try:
            with open(hist_path, "r", encoding="utf-8") as fh:
                records = json.load(fh)
            if isinstance(records, list):
                known = {r.get("session_id", "") for r in records
                         if isinstance(r, dict)}
        except (json.JSONDecodeError, OSError, ValueError):
            known = None

    removed = reclaimed = 0
    for entry in sess_dir.iterdir():
        try:
            if not entry.is_file():
                continue
            name = entry.name
            if name.endswith(".tmp") and name.startswith("."):
                pass
            elif name.startswith("session_") and name.endswith(".json"):
                if known is None:
                    continue
                if name[len("session_"):-len(".json")] in known:
                    continue
            else:
                continue
            stat = entry.stat()
            if now - stat.st_mtime < _STALE_AFTER_SECONDS:
                continue
            size = stat.st_size
            entry.unlink()
        except OSError:
            continue
        removed += 1
        reclaimed += size
    return removed, reclaimed


def compact_oversized_sessions(now: float | None = None) -> tuple[int, int]:
    """Rewrite retained session files whose findings array is already ignored.

    Returns ``(files_compacted, bytes_reclaimed)``.

    sweep_orphaned_files only takes files the index does *not* name, so a
    session listed in history.json is protected however large it is. Three
    files written before Podbye stopped persisting raw findings held 3.42 GB of
    a 3.44 GB folder that way, and they only drop off after MAX_ANALYZE_HISTORY
    further scans push them out of the index.

    Nothing is lost by rewriting them: _load_session_file already reads
    anything past _SKIP_FINDINGS_ABOVE_BYTES with _read_skipping_findings, so
    those findings are discarded on every read anyway. Compacting just stops
    paying disk for bytes the loader is contractually unable to return. The
    same threshold governs both, which keeps the invariant simple — if the
    loader would throw the findings away, they are not kept on disk.

    Files below the threshold are left alone: the win there is a fraction of a
    megabyte and would cost a full parse-and-rewrite of every session on every
    startup.
    """
    now = time.time() if now is None else now
    sess_dir = _sessions_dir()
    if not sess_dir.is_dir():
        return 0, 0

    compacted = reclaimed = 0
    for entry in sess_dir.iterdir():
        try:
            if not entry.is_file():
                continue
            name = entry.name
            if not (name == "last_run.json"
                    or (name.startswith("session_") and name.endswith(".json"))):
                continue
            stat = entry.stat()
            if stat.st_size <= _SKIP_FINDINGS_ABOVE_BYTES:
                continue
            # Another instance may be mid-scan writing this very session.
            if now - stat.st_mtime < _STALE_AFTER_SECONDS:
                continue

            data = _read_skipping_findings(entry)
            if data is None:
                # Unexpected layout. A full parse of a multi-gigabyte file is
                # exactly the freeze this whole path exists to avoid, so leave
                # it: the loader cannot read it either, and the index will
                # eventually evict it.
                continue
            data["findings"] = []
            data["findings_omitted"] = True
            data["entities"] = _strip_derived_fields(
                data.get("entities") or [], _DERIVED_ENTITY_KEYS)

            # Reading a multi-gigabyte file takes seconds, and a scan that
            # started meanwhile can checkpoint last_run.json in that window.
            # os.replace would swap in our older content over the newer save,
            # so re-check the file we actually read is still the one on disk.
            if entry.stat().st_mtime != stat.st_mtime:
                continue
            _write_json_atomic(entry, data)
            reclaimed += stat.st_size - entry.stat().st_size
        except (OSError, MemoryError, ValueError):
            continue
        compacted += 1
    return compacted, reclaimed


# ── Snapshot slimming ────────────────────────────────────────────

# Keys written into a session file that no reader ever reads back.
#
# Every load path goes restore_from_session() -> SmartEntity/Finding ->
# to_dict(), so display text is rebuilt from the model on the way to the
# screen. Persisting it too cost ~40% of a session file (`why` alone was
# 198 KB of a 2.2 MB snapshot) and actively hurt: `why`, `recommendation` and
# the *_label fields are user-facing prose frozen in whatever language the
# scan ran in, so a session reopened after a language switch rendered stale
# text. Dropping them makes reopened sessions follow the current language.
#
# Anything listed here MUST be derivable from the fields that survive — see
# SmartEntity.__post_init__ / its properties, and Finding.__post_init__.
_DERIVED_ENTITY_KEYS = frozenset({
    "category",           # property of entity_type + origin
    "is_dir", "is_entity",  # constants for every entity
    "size", "age",        # formatted from size_bytes / modified
    "source_rule",        # f"entity detection: {entity_type}"
    "why", "recommendation",
    "entity_type_label",  # ENTITY_TYPES lookup
    "actionability",      # property of entity_type + risk
    "confidence_label",   # property of confidence_score
    "last_access", "first_seen",  # formatted from accessed / modified
    "reclaimable_bytes",  # recomputed; the per-session total is stored instead
})

# Findings keep category/risk/source_rule: re-deriving those means re-running
# categorize() over up to _LARGE_SCAN_THRESHOLD findings on restore, which is
# real work, unlike the formatting below.
_DERIVED_FINDING_KEYS = frozenset({
    "size", "age",
    "why", "recommendation",
    "last_access", "first_seen",
    "reclaimable_bytes",
})


def _strip_derived_fields(items: list[dict], derived: frozenset) -> list[dict]:
    """Drop re-derivable and default-valued keys from serialized items.

    Empty strings and empty lists go too — every reader fetches these with a
    matching ``.get(key, "")`` / ``.get(key, [])`` default, and on a real scan
    most entities carry a dozen empty app-metadata fields at ~25 bytes of JSON
    each.
    """
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        out.append({
            k: v for k, v in item.items()
            if k not in derived and v != "" and v != []
        })
    return out


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


def _read_skipping_findings(path: Path) -> dict | None:
    """Parse a session file with its top-level "findings" array replaced by [].

    A full C:/ scan produced by an older build stores ~1.6M raw findings in a
    1.7 GB file. json.load() on that allocates gigabytes and blocks the caller
    for minutes — that is what froze the app when the user reopened a big scan.

    Nothing on screen needs the raw array: Findings renders entities, and new
    sessions no longer persist findings for a large scan at all. So the array is
    skipped at the byte level. We keep the (small) text before it, seek past the
    array without decoding a single entry, keep the text after it, and parse the
    result. Peak memory tracks the entities, not the file.

    Returns None when the file does not have the expected shape; the caller
    decides whether falling back to a full parse is affordable.
    """
    with open(path, "rb") as f:
        head = b""
        while True:
            chunk = f.read(_READ_CHUNK)
            if not chunk:
                return None
            head += chunk
            key_at = head.find(_FINDINGS_KEY)
            if key_at != -1:
                break
            if len(head) > _MAX_FINDINGS_KEY_OFFSET:
                return None

        before = head[:key_at]
        window = head[key_at + len(_FINDINGS_KEY):]

        # An empty array is written inline as "[]", so there is no multi-line
        # block to skip — and searching for a closing bracket would run on into
        # the next top-level array and corrupt the document.
        if window.lstrip()[:2] == b"[]":
            tail = window
        else:
            overlap = len(_TOP_LEVEL_ARRAY_END) - 1
            while True:
                end_at = window.find(_TOP_LEVEL_ARRAY_END)
                if end_at != -1:
                    # Drop the array body; keep "[]" plus everything after it.
                    tail = b" []" + window[end_at + len(_TOP_LEVEL_ARRAY_END):]
                    break
                chunk = f.read(_READ_CHUNK)
                if not chunk:
                    return None
                window = window[-overlap:] + chunk
        tail += f.read()

    try:
        data = json.loads((before + _FINDINGS_KEY + tail).decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _load_session_file(path: Path) -> dict | None:
    """Load a session snapshot from *path*, without freezing on legacy files."""
    if not path.exists():
        return None
    try:
        if path.stat().st_size > _SKIP_FINDINGS_ABOVE_BYTES:
            data = _read_skipping_findings(path)
            if data is not None:
                data["findings_omitted"] = True
                return data
            # Unexpected layout — every file this app writes matches the fast
            # path, so this is a last resort. A full parse is slow and hungry,
            # but callers run this off the UI thread behind BusyDialog, so it
            # costs the user time rather than a frozen window.
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, OSError, MemoryError):
        pass
    return None


def load_session() -> dict | None:
    """Load last_run.json. Returns dict or None if missing/corrupt."""
    return _load_session_file(_last_run_path())


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


# ─── What the AI actually did to a session ───────────────────────
# ai_status on an entity (see models/smart_entity.py). "none" and "disabled"
# mean the item never entered the queue — they are not work that is pending.
_AI_EXPLAINED = ("ready", "done")
_AI_IN_FLIGHT = ("pending", "analyzing")
_AI_ATTEMPTED = _AI_EXPLAINED + _AI_IN_FLIGHT + ("failed", "error", "cancelled")


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
    # Only items that actually entered the AI queue may appear in a
    # denominator. Bulk AI is off by default (ai_findings_enabled) and per-item
    # "Ask AI" is the normal path, so on a stock install nothing is queued —
    # counting every finding invented a queue of work nobody had asked for.
    ai_ready_count = sum(1 for it in display_items
                         if it.get("ai_status") in _AI_EXPLAINED)
    ai_attempted_count = sum(1 for it in display_items
                             if it.get("ai_status") in _AI_ATTEMPTED)
    ai_active_count = sum(1 for it in display_items
                          if it.get("ai_status") in _AI_IN_FLIGHT)
    summary.update({
        "last_update": data.get("last_update", 0.0),
        "saved_at": data.get("saved_at", time.time()),
        "has_entities": bool(entities),
        "display_count": display_count,
        "display_unit": display_unit,
        "ai_ready_count": ai_ready_count,
        "ai_attempted_count": ai_attempted_count,
        "ai_active_count": ai_active_count,
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
    # Keep the stored historical verdict in step with the classifier History
    # uses. A run that removed some files and then hit an error is partial,
    # rather than a total failure.
    from app.services.cleanup_result_classifier import assess_cleanup_counts
    succeeded_count = len(result.succeeded)
    in_use_count = len(getattr(result, "in_use", []))
    failed_count = len(result.failed)
    skipped_count = len(result.skipped_protected)
    result_state = assess_cleanup_counts(
        succeeded_count=succeeded_count,
        in_use_count=in_use_count,
        failed_count=failed_count,
        skipped_count=skipped_count,
    ).state
    data = {
        "type": "cleanup",
        "timestamp": ts,
        "session_id": session_id,
        "mode": mode,
        "total_bytes_freed": result.total_bytes_freed,
        "succeeded_count": succeeded_count,
        "in_use_count": in_use_count,
        "failed_count": failed_count,
        "skipped_protected_count": skipped_count,
        "result_state": result_state,
        "items": items,
        # Marks that each item's "size" is its own bytes. Records written
        # before 2026-08-20 carry the *bucket's* total on every one of its
        # members — a nine-file cleanup recorded nine identical 668 MB items
        # for 3.9 GB actually freed — and History cannot tell the two apart by
        # looking, so it declines to add up the ones without this stamp.
        "item_sizes": "measured",
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
    return _load_session_file(_session_file_path(session_id))


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

    Item dicts are slimmed by _strip_derived_fields on the way in, so the
    snapshot is what gets *persisted* — not what gets displayed. Callers that
    want display-ready dicts go through the model's to_dict().
    """
    entities_dicts = entities_dicts or []
    # Summed before stripping: the per-item value is derived, but History reads
    # the session total and cannot recompute it without the models.
    total_reclaimable = _sum_reclaimable(entities_dicts or findings_dicts)
    return {
        "session_id": session_id,
        "target": target,
        "scan_mode": scan_mode,
        "status": status,
        "start_time": start_time,
        "last_update": time.time(),
        "scanned_count": scanned_count,
        "total_size": total_size,
        "total_reclaimable_bytes": total_reclaimable,
        "category_totals": category_totals,
        "risk_totals": risk_totals,
        "findings": _strip_derived_fields(findings_dicts, _DERIVED_FINDING_KEYS),
        # True when the raw per-file list was too large to persist. Findings
        # renders entities, so this only tells a resume it cannot dedup by path.
        "findings_omitted": findings_omitted,
        "entities": _strip_derived_fields(entities_dicts, _DERIVED_ENTITY_KEYS),
        "scan_frontier": scan_frontier or [],
    }
