"""Filesystem scanner — runs in a QThread, emits results to ScanState."""
from __future__ import annotations

import os
import time

from PySide6.QtCore import QThread, Signal

from app.models.finding import Finding


# Batch size: emit findings every N items to avoid overwhelming the UI
_BATCH_SIZE = 500

# Directories to never descend into (always skipped silently)
_SKIP_DIRS = {
    "$recycle.bin", "system volume information", ".git",
}

# Reason codes for structured skipped entries
SKIP_REASON_PERMISSION  = "Permission denied"
SKIP_REASON_LOCKED      = "Locked by system"
SKIP_REASON_ENCRYPTED   = "Encrypted or unavailable"
SKIP_REASON_LOOP        = "Symbolic link / junction (skipped to prevent loops)"

# Known root-level protected folders → human description for AI / UI
_KNOWN_PROTECTED: dict[str, str] = {
    "windowsapps":                "Microsoft Store application storage",
    "windows":                    "Windows operating system files",
    "system volume information":  "Windows restore and indexing data",
    "$windows.~bt":               "Windows upgrade staging files",
    "$windows.~ws":               "Windows upgrade workspace",
    "program files":              "Installed 64-bit applications",
    "program files (x86)":        "Installed 32-bit applications",
    "programdata":                "Shared application data",
    "recovery":                   "Windows recovery partition data",
    "boot":                       "Boot configuration files",
    "perflogs":                   "Performance log files",
    "msocache":                   "Microsoft Office installation cache",
    "config.msi":                 "Windows Installer temporary data",
    "documents and settings":     "Legacy user profile junction",
}


class ScanWorker(QThread):
    """Recursive filesystem scanner running off the main thread.

    Signals:
        batch_ready(list)       — a batch of Finding objects
        progress(int, str)      — (scanned_count, current_path)
        log(str)                — operator feed line
        skipped(list)           — batch of skipped-entry dicts
                                  keys: path, name, reason, description
        finished_scan()         — scan completed (normal or halted)
    """

    batch_ready = Signal(list)
    progress = Signal(int, str)
    log = Signal(str)
    skipped = Signal(list)  # list of dicts: {path, name, reason, description}
    finished_scan = Signal()

    def __init__(self, target: str, skip_paths: set = None, parent=None):
        super().__init__(parent)
        self._target = target
        self._halt = False
        self._scanned = 0
        self._skipped_perms = 0
        self._skipped_symlinks = 0
        self._skipped_known = 0
        self._seen_realpaths: set = set()
        self._skip_paths: set = skip_paths or set()  # normalized paths to skip (resume)
        self._skipped_batch: list = []               # pending structured skipped entries
        self._skipped_total: int = 0                 # total protected/skipped items

    def halt(self):
        """Request graceful stop."""
        self._halt = True

    def run(self):
        self.log.emit(f"[scan] starting recursive walk: {self._target}")
        self.log.emit("[scan] symlinks/junctions: skipped (not followed)")
        t0 = time.time()
        batch: list = []
        self._root_depth = self._target.replace("\\", "/").count("/")

        try:
            for dirpath, dirnames, filenames in os.walk(
                self._target, topdown=True, followlinks=False
            ):
                if self._halt:
                    self.log.emit("[scan] halted by user — partial results preserved")
                    break

                # ── Symlink / junction / realpath safety ──
                try:
                    # Check if this directory itself is a symlink/junction
                    if os.path.islink(dirpath):
                        self._skipped_symlinks += 1
                        self._record_skipped(dirpath, SKIP_REASON_LOOP)
                        dirnames.clear()
                        continue

                    real = os.path.realpath(dirpath)
                    norm = os.path.normcase(os.path.normpath(real))
                    if norm in self._seen_realpaths:
                        dirnames.clear()
                        continue
                    self._seen_realpaths.add(norm)
                except OSError:
                    dirnames.clear()
                    continue

                # ── Prune dirs we should never enter ──
                pruned = []
                for d in dirnames:
                    full = os.path.join(dirpath, d)
                    if d.lower() in _SKIP_DIRS:
                        pruned.append(d)
                    elif os.path.islink(full):
                        self._skipped_symlinks += 1
                        self._record_skipped(full, SKIP_REASON_LOOP)
                        pruned.append(d)
                dirnames[:] = [d for d in dirnames if d not in pruned]

                # ── Process directory itself ──
                # Record ALL directories so the entity detector can build a
                # complete children_index hierarchy and correctly claim nested paths.
                # Without this, any directory deeper than depth 1 is absent from
                # children_index, _claim() cannot reach its files, and every file
                # inside ends up as a loose "Misc files in X" Unknown entity.
                finding = self._stat_entry(dirpath, is_dir=True)
                if finding:
                    if self._should_skip(finding.path):
                        self._skipped_known += 1
                    else:
                        batch.append(finding)
                        self._scanned += 1

                # ── Process files ──
                for fname in filenames:
                    if self._halt:
                        break
                    fpath = os.path.join(dirpath, fname)

                    # Skip file symlinks
                    if os.path.islink(fpath):
                        self._skipped_symlinks += 1
                        continue

                    finding = self._stat_entry(fpath, is_dir=False)
                    if finding:
                        if self._should_skip(finding.path):
                            self._skipped_known += 1
                            continue
                        batch.append(finding)
                        self._scanned += 1

                    # Emit progress periodically (every 2000 items to reduce signal spam)
                    if self._scanned % 2000 == 0:
                        self.progress.emit(self._scanned, self._shorten(dirpath))

                    # Flush batch
                    if len(batch) >= _BATCH_SIZE:
                        self.batch_ready.emit(batch[:])
                        batch.clear()

                # Log directory progress periodically (every 25k items)
                if self._scanned % 25_000 == 0 and self._scanned > 0:
                    elapsed = time.time() - t0
                    rate = self._scanned / max(elapsed, 0.01)
                    self.log.emit(
                        f"[scan] {self._scanned:,} items · {elapsed:.1f}s · "
                        f"{rate:.0f}/s"
                    )

        except Exception as exc:
            self.log.emit(f"[warning] scan failed: {exc}")

        # Flush remaining
        if batch:
            self.batch_ready.emit(batch[:])
            batch.clear()

        # Flush any remaining skipped entries
        self._flush_skipped(force=True)

        elapsed = time.time() - t0
        rate = self._scanned / max(elapsed, 0.01)
        status = "halted" if self._halt else "complete"
        parts = [
            f"[scan] {status} · {self._scanned:,} items · {elapsed:.1f}s · {rate:.0f}/s",
        ]
        if self._skipped_total > 0:
            parts.append(f"{self._skipped_total:,} protected/skipped")
        if self._skipped_symlinks > 0:
            parts.append(f"{self._skipped_symlinks} symlinks")
        if self._skipped_known > 0:
            parts.append(f"{self._skipped_known:,} already known (resume)")
        self.log.emit(" · ".join(parts))
        self.progress.emit(self._scanned, "done")
        self.finished_scan.emit()

    def _record_skipped(self, path: str, reason: str):
        """Record a structured skipped entry and flush in batches."""
        name = os.path.basename(path)
        lower = name.lower()
        description = _KNOWN_PROTECTED.get(lower, "")
        entry = {
            "path": path,
            "name": name,
            "reason": reason,
            "description": description,
        }
        self._skipped_batch.append(entry)
        self._skipped_total += 1
        self._flush_skipped()

    def _flush_skipped(self, force: bool = False):
        """Emit pending skipped entries in batches of 50."""
        if self._skipped_batch and (force or len(self._skipped_batch) >= 50):
            self.skipped.emit(self._skipped_batch[:])
            self._skipped_batch.clear()

    def _stat_entry(self, path: str, is_dir: bool):
        """Stat a single file/dir, return Finding or None on error."""
        try:
            st = os.stat(path, follow_symlinks=False)
        except (OSError, PermissionError):
            self._skipped_perms += 1
            self._record_skipped(path, SKIP_REASON_PERMISSION)
            return None

        name = os.path.basename(path)
        ext = os.path.splitext(name)[1] if not is_dir else ""
        # Directories get size 0 at scan time — entity detector aggregates sizes.
        # This avoids expensive re-scanning of directory children during the walk.
        size = st.st_size if not is_dir else 0

        return Finding(
            path=path,
            name=name,
            is_dir=is_dir,
            size_bytes=size,
            extension=ext,
            modified=st.st_mtime,
            accessed=getattr(st, 'st_atime', st.st_mtime),
            parent=os.path.dirname(path),
        )

    def _should_skip(self, path: str) -> bool:
        """Check if a path was already known (resume dedup)."""
        if not self._skip_paths:
            return False
        return path.replace("\\", "/").lower() in self._skip_paths

    @staticmethod
    def _shorten(path: str, max_len: int = 60) -> str:
        """Shorten a path for display."""
        if len(path) <= max_len:
            return path
        parts = path.replace("\\", "/").split("/")
        if len(parts) <= 3:
            return path
        return parts[0] + "/.../" + "/".join(parts[-2:])
