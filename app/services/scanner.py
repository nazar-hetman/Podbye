"""Filesystem scanner — runs in a QThread, emits results to ScanState."""
from __future__ import annotations

import os
import sys
import time

from PySide6.QtCore import QThread, Signal

from app.models.finding import Finding


# Batch size: emit findings every N items to avoid overwhelming the UI. Larger
# batches mean fewer cross-thread signal deliveries — important now that the
# scandir walk produces items far faster than the old os.walk did.
_BATCH_SIZE = 2000

# Windows file-attribute bits that mean "the data is not stored on this disk".
# Cloud sync providers (OneDrive "files on-demand", etc.) leave a placeholder
# whose logical st_size is the full file but whose on-disk footprint is ~0.
# Counting st_size for these is exactly why a scan can report more bytes than
# the volume physically holds. We read st_file_attributes (already returned by
# the os.stat call below — no extra syscall) and treat such files as 0 bytes.
_FILE_ATTRIBUTE_OFFLINE               = 0x00001000
_FILE_ATTRIBUTE_RECALL_ON_OPEN        = 0x00040000
_FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x00400000
_CLOUD_PLACEHOLDER_MASK = (
    _FILE_ATTRIBUTE_OFFLINE
    | _FILE_ATTRIBUTE_RECALL_ON_OPEN
    | _FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
)

# Directories to never descend into (always skipped silently)
_SKIP_DIRS = {
    "$recycle.bin", "system volume information", ".git",
}

# Per-folder metadata the OS writes and rewrites on its own. These are a few
# hundred bytes each, cannot be meaningfully deleted (Windows recreates them),
# and are never what a user is looking for — but they count as files, so an
# otherwise-empty C:/Users/<u>/Videos was reported as a "Videos / Movies"
# collection on the strength of its desktop.ini, and C:/Users/<u>/Saved Games
# holds nothing else at all.
_OS_METADATA_FILES = {
    "desktop.ini",      # folder icon / localized name (Windows)
    "thumbs.db",        # Explorer thumbnail cache (Windows)
    "ehthumbs.db",
    ".ds_store",        # Finder folder state (macOS, common on shared drives)
    "icon\r",           # macOS custom folder icon
}

# Reason codes for structured skipped entries
SKIP_REASON_PERMISSION  = "Permission denied"
SKIP_REASON_LOCKED      = "Locked by system"
SKIP_REASON_ENCRYPTED   = "Encrypted or unavailable"
SKIP_REASON_LOOP        = "Symbolic link / junction (skipped to prevent loops)"
SKIP_REASON_VOLUME      = "Different drive / volume (not followed)"

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
        frontier_update(list)   — snapshot of directories discovered but not yet
                                  walked (the resume frontier); [] at completion
    """

    batch_ready = Signal(list)
    progress = Signal(int, str)
    log = Signal(str)
    skipped = Signal(list)  # list of dicts: {path, name, reason, description}
    finished_scan = Signal()
    frontier_update = Signal(list)  # pending-directory stack for resume

    # Minimum seconds between frontier snapshots (emitted at dir boundaries).
    _FRONTIER_INTERVAL_S = 3.0

    def __init__(self, target: str, skip_paths: set = None,
                 cross_volumes: bool = False, resume_stack: list = None,
                 roots: list = None, parent=None):
        super().__init__(parent)
        self._target = target
        # Roots to walk. A single-folder scan has one; "Scan all drives" seeds
        # the walk with every fixed-drive root. `target` stays the display label
        # ("All drives"), so the rest of the pipeline is unchanged.
        self._roots = [r for r in (roots or [target]) if r]
        self._halt = False
        self._scanned = 0
        self._skipped_perms = 0
        self._skipped_symlinks = 0
        self._skipped_known = 0
        self._skipped_volume = 0
        self._cross_volumes = cross_volumes  # follow into other drives/volumes?
        self._root_devs = None               # st_dev set of the seeded roots
        self._skip_paths: set = skip_paths or set()  # normalized paths to skip (resume)
        # Directories left to walk from a previous (interrupted) run. When set,
        # the walk continues from this frontier instead of re-walking from root.
        self._resume_stack: list = resume_stack or []
        self._skipped_batch: list = []               # pending structured skipped entries
        self._skipped_total: int = 0                 # total protected/skipped items

    def halt(self):
        """Request graceful stop."""
        self._halt = True

    def run(self):
        if len(self._roots) > 1:
            self.log.emit(f"[scan] starting recursive walk: {len(self._roots)} "
                          f"roots ({', '.join(self._roots)})")
        else:
            self.log.emit(f"[scan] starting recursive walk: {self._target}")
        self.log.emit("[scan] symlinks/junctions: skipped (not followed)")
        if not self._cross_volumes:
            # Allow every seeded root's own volume, prune anything else (junction
            # or mount into a volume the user did not choose). A set so a
            # multi-drive scan keeps each drive without crossing into others.
            self._root_devs = set()
            for r in self._roots:
                try:
                    self._root_devs.add(os.stat(r).st_dev)
                except OSError:
                    pass
            self.log.emit("[scan] cross-volume descent: off (staying on the "
                          "selected drive(s))")
        else:
            self._root_devs = None
            self.log.emit("[scan] cross-volume descent: on")
        t0 = time.time()
        batch: list = []
        self._last_log = 0  # _scanned value at the last periodic progress log
        last_frontier = 0.0  # time of the last frontier snapshot emit

        # Iterative depth-first walk via os.scandir. Each DirEntry already
        # carries type, size, mtime and (on Windows) file attributes from the
        # single directory read the OS performed — so files cost no extra
        # syscalls. We pay one os.stat per *directory* only for the cross-volume
        # guard (st_dev isn't populated by scandir on Windows), and only when
        # that guard is active — i.e. default, non cross-volume scans.
        if self._resume_stack:
            # Continuation run: pick up exactly where we stopped. Findings for
            # everything already walked were restored into ScanState, and
            # _should_skip() dedups any overlap, so we never re-record them.
            stack: list[str] = list(self._resume_stack)
            self.log.emit(
                f"[scan] resuming from {len(stack):,} pending director"
                f"{'y' if len(stack) == 1 else 'ies'} (skipping re-walk)"
            )
        else:
            # Fresh run: record each scan-root directory itself, then descend.
            stack = list(self._roots)
            for root in self._roots:
                root_finding = self._stat_entry(root, is_dir=True)
                if root_finding:
                    if self._should_skip(root_finding.path):
                        self._skipped_known += 1
                    else:
                        batch.append(root_finding)
                        self._scanned += 1

        try:
            while stack:
                if self._halt:
                    self.log.emit("[scan] halted by user — partial results preserved")
                    break
                dirpath = stack.pop()

                try:
                    scan_it = os.scandir(dirpath)
                except OSError:
                    self._skipped_perms += 1
                    self._record_skipped(dirpath, SKIP_REASON_PERMISSION)
                    continue

                with scan_it:
                    for entry in scan_it:
                        if self._halt:
                            break

                        try:
                            is_dir = entry.is_dir(follow_symlinks=False)
                        except OSError:
                            is_dir = False
                        # Symlinks / junctions / mount points are never followed
                        # (loop safety). is_symlink() is True for all Windows
                        # name-surrogate reparse points (junctions included).
                        try:
                            is_link = entry.is_symlink()
                        except OSError:
                            is_link = False

                        if is_dir:
                            if is_link:
                                self._skipped_symlinks += 1
                                self._record_skipped(entry.path, SKIP_REASON_LOOP)
                                continue
                            if entry.name.lower() in _SKIP_DIRS:
                                continue
                            if (self._root_devs is not None
                                    and self._crosses_volume(entry.path)):
                                # Different drive/volume (mounted disk, junction
                                # to another volume) — don't descend unless the
                                # user opted into cross-volume scans.
                                self._skipped_volume += 1
                                self._record_skipped(entry.path, SKIP_REASON_VOLUME)
                                continue
                            # Record the directory so the entity detector can
                            # build a complete children_index, then queue it for
                            # descent.
                            finding = self._finding_from_entry(entry, is_dir=True)
                            if finding:
                                if self._should_skip(finding.path):
                                    self._skipped_known += 1
                                else:
                                    batch.append(finding)
                                    self._scanned += 1
                            stack.append(entry.path)
                        else:
                            if is_link:
                                self._skipped_symlinks += 1
                                continue
                            if entry.name.lower() in _OS_METADATA_FILES:
                                continue
                            finding = self._finding_from_entry(entry, is_dir=False)
                            if finding:
                                if self._should_skip(finding.path):
                                    self._skipped_known += 1
                                else:
                                    batch.append(finding)
                                    self._scanned += 1

                        # Emit progress periodically (reduce signal spam).
                        if self._scanned % 2000 == 0:
                            self.progress.emit(self._scanned, self._shorten(dirpath))

                        # Flush batch.
                        if len(batch) >= _BATCH_SIZE:
                            self.batch_ready.emit(batch[:])
                            batch.clear()

                # If halted mid-directory, this dir wasn't fully enumerated —
                # re-queue it so a resume re-walks it (already-recorded entries
                # are deduped by _should_skip), then stop.
                if self._halt:
                    stack.append(dirpath)
                    break

                # Log progress periodically (~every 25k items).
                if self._scanned - self._last_log >= 25_000:
                    self._last_log = self._scanned
                    elapsed = time.time() - t0
                    rate = self._scanned / max(elapsed, 0.01)
                    self.log.emit(
                        f"[scan] {self._scanned:,} items · {elapsed:.1f}s · "
                        f"{rate:.0f}/s"
                    )

                # Publish the resume frontier at clean directory boundaries,
                # throttled. Flush pending findings first so persisted findings
                # always cover everything up to the frontier (never behind it).
                now = time.time()
                if now - last_frontier >= self._FRONTIER_INTERVAL_S:
                    last_frontier = now
                    if batch:
                        self.batch_ready.emit(batch[:])
                        batch.clear()
                    self.frontier_update.emit(list(stack))

        except Exception as exc:
            self.log.emit(f"[warning] scan failed: {exc}")

        # Flush remaining
        if batch:
            self.batch_ready.emit(batch[:])
            batch.clear()

        # Flush any remaining skipped entries
        self._flush_skipped(force=True)

        # Final frontier: the directories still pending. Empty on a completed
        # scan (not resumable); non-empty when halted (resume continues here).
        self.frontier_update.emit(list(stack))

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
        if self._skipped_volume > 0:
            parts.append(f"{self._skipped_volume} on other volumes")
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

    def _make_finding(self, path: str, name: str, st, is_dir: bool) -> Finding:
        """Build a Finding from an already-obtained stat result.

        Shared by the root path-stat and the per-entry scandir path so the
        size/cloud-placeholder logic stays identical for both.
        """
        # Intern the two highly-repetitive strings. A C:/ scan holds ~1.8M
        # findings live, but they share only ~326k parent dirs and a few
        # thousand extensions — and dirname()/splitext() hand back a fresh
        # object every call, so each duplicate was paying for its own string.
        # Measured on a real C:/ scan: ~211 MB (15%) saved.
        ext = sys.intern(os.path.splitext(name)[1]) if not is_dir else ""
        # Directories get size 0 at scan time — the entity detector aggregates
        # sizes, so we avoid re-summing children during the walk. Cloud
        # placeholders (OneDrive files-on-demand) report a full logical size but
        # occupy ~0 bytes on disk — count them as 0 so the on-disk total stays
        # honest. st_file_attributes comes free with the stat we already have.
        attrs = getattr(st, "st_file_attributes", 0)
        cloud_only = bool(attrs & _CLOUD_PLACEHOLDER_MASK)
        size = 0 if (is_dir or cloud_only) else st.st_size

        return Finding(
            path=path,
            name=name,
            is_dir=is_dir,
            size_bytes=size,
            extension=ext,
            modified=st.st_mtime,
            accessed=getattr(st, 'st_atime', st.st_mtime),
            parent=sys.intern(os.path.dirname(path)),
            cloud_only=cloud_only,
        )

    def _stat_entry(self, path: str, is_dir: bool):
        """Stat a single file/dir by path (used for the scan root)."""
        try:
            st = os.stat(path, follow_symlinks=False)
        except (OSError, PermissionError):
            self._skipped_perms += 1
            self._record_skipped(path, SKIP_REASON_PERMISSION)
            return None
        return self._make_finding(path, os.path.basename(path), st, is_dir)

    def _finding_from_entry(self, entry: "os.DirEntry", is_dir: bool):
        """Build a Finding from a scandir DirEntry using its cached stat —
        no extra syscall on the common path."""
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError:
            self._skipped_perms += 1
            self._record_skipped(entry.path, SKIP_REASON_PERMISSION)
            return None
        return self._make_finding(entry.path, entry.name, st, is_dir)

    def _crosses_volume(self, path: str) -> bool:
        """True if *path* sits on a volume that was not one of the scan roots.

        Uses st_dev (the volume identifier). Fail-open: a stat error returns
        False so a single unreadable entry is not silently pruned here — the
        normal stat path will record it as permission-denied instead.
        """
        try:
            return os.stat(path).st_dev not in self._root_devs
        except OSError:
            return False

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
