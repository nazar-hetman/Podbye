"""Duplicate detection — background content-hash worker.

Hashes files above a configurable size threshold (default 10 MB), groups
identical files by hash, and emits one SmartEntity(duplicate_group) per group.

Design constraints:
- Runs in a QThread — never blocks the main thread.
- Only hashes files > threshold_mb (smaller files have low ROI).
- Uses SHA-256 (BLAKE3 used instead if the ``blake3`` package is installed).
- One failure (PermissionError, OSError) skips that file; doesn't abort.
- Halts cleanly when halt() is called between chunks.
"""
from __future__ import annotations

import hashlib
import os
import time

from PySide6.QtCore import QThread, Signal

from app.models.finding import _format_size
from app.models.smart_entity import SmartEntity

_CHUNK = 65_536  # 64 KB read chunks


def _safe_date(ts: float) -> str:
    try:
        if ts <= 0:
            return "—"
        return time.strftime("%Y-%m-%d", time.localtime(ts))
    except Exception:
        return "—"


def _duplicate_safety(files: list) -> tuple[str, str]:
    """Return duplicate cleanup risk with ownership-aware safety guards."""
    paths = [
        getattr(f, "path", "").replace("\\", "/").lower()
        for f in files
    ]
    system_segments = (
        "/windows/system32", "/windows/syswow64", "/windows/winsxs",
        "/windows/installer", "/windows/servicing", "/windows/systemapps",
        "/windows/fonts", "/windows/assembly", "/windows/microsoft.net",
    )
    if any(seg in p for p in paths for seg in system_segments):
        return "Protected", "duplicate group inside Windows-managed system files"

    if any("/windows/" in p for p in paths):
        return "Protected", "duplicate group inside Windows-owned files"

    app_segments = (
        "/program files/", "/program files (x86)/", "/_internal/",
        "/runtimes/", "/runtime/", "/site-packages/", "/dist-packages/",
        "/torch/lib/", "/library/bin/", "/library/lib/",
    )
    if any(seg in p for p in paths for seg in app_segments):
        return "Review", "duplicate group inside installed application or bundled runtime"

    return "Optional", ""


def _used_by_summary(paths: list[str], limit: int = 3) -> str:
    """Compact location summary for duplicate rows and AI context."""
    all_parents: list[str] = []
    seen: set[str] = set()
    for path in paths:
        parent = os.path.dirname(path)
        if not parent:
            parent = path
        key = parent.replace("\\", "/").lower()
        if key in seen:
            continue
        seen.add(key)
        all_parents.append(parent)
    parents = all_parents[:limit]
    if not parents:
        return "locations unknown"
    extra = max(0, len(all_parents) - len(parents))
    text = " · ".join(parents)
    if extra:
        text += f" · +{extra} more"
    return text


def _hash_file(path: str, halt_fn) -> str | None:
    """Return hex digest for file at path, or None on error / halt."""
    try:
        try:
            import blake3  # type: ignore
            h = blake3.blake3()
        except ImportError:
            h = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                if halt_fn():
                    return None
                chunk = f.read(_CHUNK)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


class DuplicateDetector(QThread):
    """Background worker: hash large files, emit one entity per duplicate group.

    Signals
    -------
    group_found(SmartEntity)
        Emitted for each group of 2+ identical files. Entity type is
        ``duplicate_group``; ``dup_reclaimable`` carries bytes of all-but-newest.
    progress(int, int)
        (hashed_so_far, total_to_hash) — suitable for a progress bar.
    finished(int, int)
        (groups_found, total_reclaimable_bytes) — emitted after all hashing.
    log_line(str)
        Operator-feed log messages.
    """

    group_found = Signal(object)      # SmartEntity
    progress    = Signal(int, int)    # hashed, total
    # reclaimable_bytes is a byte total that easily exceeds 2**31 (a plain
    # Signal(int) is a 32-bit C++ int and overflows → OverflowError on emit).
    finished    = Signal(int, "qint64")  # groups_count, reclaimable_bytes
    log_line    = Signal(str)

    DEFAULT_THRESHOLD_MB = 10

    def __init__(self, findings, threshold_mb: int = DEFAULT_THRESHOLD_MB, parent=None):
        """
        Parameters
        ----------
        findings : list[Finding]
            Raw findings from the scan (Finding objects, not dicts).
        threshold_mb : int
            Only files larger than this (in MB) are hashed.
        """
        super().__init__(parent)
        self._findings = findings
        self._threshold = threshold_mb * 1024 * 1024
        self._halt_flag = False

    def halt(self):
        self._halt_flag = True

    def run(self):
        t0 = time.time()
        # Collect candidate files
        candidates = [
            f for f in self._findings
            if not getattr(f, "is_dir", True)
            and getattr(f, "size_bytes", 0) >= self._threshold
        ]
        total = len(candidates)
        if total == 0:
            self.log_line.emit("[dedup] no files above threshold — skipping")
            self.finished.emit(0, 0)
            return

        self.log_line.emit(
            f"[dedup] hashing {total} files ≥ {self.DEFAULT_THRESHOLD_MB} MB…"
        )

        hash_groups: dict[str, list] = {}
        for i, f in enumerate(candidates):
            if self._halt_flag:
                break
            digest = _hash_file(f.path, lambda: self._halt_flag)
            if digest:
                hash_groups.setdefault(digest, []).append(f)
            self.progress.emit(i + 1, total)

        groups_count = 0
        total_reclaimable = 0

        for digest, files in hash_groups.items():
            if len(files) < 2:
                continue
            if self._halt_flag:
                break

            # Sort newest-first so [0] is the keeper
            files.sort(key=lambda ff: getattr(ff, "modified", 0), reverse=True)
            keeper = files[0]
            duplicates = files[1:]
            dup_bytes = sum(getattr(ff, "size_bytes", 0) for ff in duplicates)
            total_size = sum(getattr(ff, "size_bytes", 0) for ff in files)

            sample_paths = [getattr(ff, "path", "") for ff in files[:6]]
            risk, safety_reason = _duplicate_safety(files)
            duplicate_locations = []
            removable_duplicate_paths = [getattr(ff, "path", "") for ff in duplicates if getattr(ff, "path", "")]
            for idx, ff in enumerate(files):
                path = getattr(ff, "path", "")
                size_bytes = getattr(ff, "size_bytes", 0)
                role = "keep candidate (newest)" if idx == 0 else "extra copy candidate"
                if risk in ("Review", "Protected") and idx > 0:
                    role = "review before removing"
                duplicate_locations.append({
                    "path": path,
                    "name": os.path.basename(path),
                    "parent": os.path.dirname(path),
                    "size_bytes": size_bytes,
                    "size": _format_size(size_bytes),
                    "modified": getattr(ff, "modified", 0),
                    "modified_display": _safe_date(getattr(ff, "modified", 0)),
                    "role": role,
                })
            risk_reason = (
                safety_reason if safety_reason else
                f"{len(files)} identical copies · keep newest · "
                f"{_format_size(dup_bytes)} reclaimable"
            )
            display_name = os.path.basename(getattr(keeper, "path", "").rstrip("\\/")) or "Duplicate files"
            used_by = _used_by_summary(sample_paths)
            summary = (
                f"{len(files)} copies · {_format_size(dup_bytes)} reclaimable · "
                f"Used by: {used_by}"
            )
            ent = SmartEntity(
                path=getattr(keeper, "path", ""),
                name=f"{display_name} · {len(files)} copies",
                entity_type="duplicate_group",
                size_bytes=total_size,
                file_count=len(files),
                folder_count=0,
                modified=getattr(keeper, "modified", 0),
                children_sample=sample_paths,
                risk=risk,
                risk_reason=risk_reason,
                summary=summary,
                dup_reclaimable=dup_bytes,
                duplicate_locations=duplicate_locations,
                removable_duplicate_paths=removable_duplicate_paths,
            )
            self.group_found.emit(ent)
            groups_count += 1
            total_reclaimable += dup_bytes

        elapsed = time.time() - t0
        self.log_line.emit(
            f"[dedup] done · {groups_count} duplicate groups · "
            f"{_format_size(total_reclaimable)} reclaimable · {elapsed:.1f}s"
        )
        self.finished.emit(groups_count, total_reclaimable)
