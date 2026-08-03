"""Cleanup engine — move files/folders to Recycle Bin or permanently delete.

Safety guarantees (enforced at execution time, not queue time):
- Protected paths ALWAYS raise ProtectedPathError — no exceptions.
- Permanent delete requires both an explicit settings flag AND no Protected items.
- Every failure is caught per-path so one locked file never aborts the batch.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as _wt
import os
import re
import shutil
from dataclasses import dataclass, field
from typing import Optional

from PySide6.QtCore import QThread, Signal


# ── Exception ────────────────────────────────────────────────────

class ProtectedPathError(Exception):
    """Raised when deletion is attempted on a protected system path."""


# ── Protected-path guard ─────────────────────────────────────────

_PROTECTED_SEGMENTS = {
    "windows", "system32", "syswow64", "winsxs",
    "programdata", "recovery", "boot",
    "$windows.~bt", "$windows.~ws",
    "msocache", "perflogs",
}


def _is_protected_for_delete(path: str) -> bool:
    """Double-check path protection at delete time.

    Mirrors the logic in entity_detector._is_protected_path() so protection
    is enforced by the engine regardless of what the UI said at selection time.
    """
    norm = path.replace("\\", "/").lower().rstrip("/")
    for part in norm.split("/"):
        if part in _PROTECTED_SEGMENTS:
            return True
    # Vigil's own install and data folders. The detector already marks these
    # protected; this is the backstop, so a stale entity from an older session
    # can never delete the app or the session store out from under a live run.
    from app.services.self_paths import is_self_path
    return is_self_path(norm)


# ── Result dataclass ─────────────────────────────────────────────

@dataclass
class CleanupResult:
    succeeded: list = field(default_factory=list)          # paths successfully recycled/deleted
    in_use: list = field(default_factory=list)             # paths skipped because Windows/app still uses them
    failed: list = field(default_factory=list)             # paths that hit unexpected errors
    skipped_protected: list = field(default_factory=list)  # paths skipped (protected)
    total_bytes_freed: int = 0
    errors_by_path: dict = field(default_factory=dict)     # path → error string
    error_codes_by_path: dict = field(default_factory=dict)  # path → parsed Windows error code


_LOCK_ERROR_CODES = {32, 33}


def _extract_windows_error_code(err: str) -> Optional[int]:
    """Best-effort parse of a Windows error code from an error string."""
    if not err:
        return None
    patterns = [
        r"winerror\s+(\d+)",
        r"error\s+0x([0-9a-fA-F]+)",
        r"error\s+(\d+)",
    ]
    lowered = err.lower()
    for pattern in patterns:
        match = re.search(pattern, lowered)
        if not match:
            continue
        try:
            base = 16 if "0x" in pattern else 10
            return int(match.group(1), base)
        except ValueError:
            return None
    return None


def _is_expected_in_use_error(err: str) -> bool:
    """Return True for common Windows file-lock/share-violation cases."""
    code = _extract_windows_error_code(err)
    if code in _LOCK_ERROR_CODES:
        return True

    lowered = (err or "").lower()
    markers = (
        "being used by another process",
        "used by another process",
        "because it is being used",
        "sharing violation",
        "file is in use",
        "directory is not empty",
    )
    return any(marker in lowered for marker in markers)


# ── Size helper ──────────────────────────────────────────────────

def _get_size(path: str) -> int:
    """Best-effort byte count of a file or directory tree."""
    try:
        if os.path.isfile(path):
            return os.path.getsize(path)
        total = 0
        for dirpath, _, filenames in os.walk(path):
            for fn in filenames:
                try:
                    total += os.path.getsize(os.path.join(dirpath, fn))
                except OSError:
                    pass
        return total
    except OSError:
        return 0


# ── SHFileOperationW (Windows Recycle Bin) ───────────────────────

class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd",                  _wt.HWND),
        ("wFunc",                 _wt.UINT),
        ("pFrom",                 ctypes.c_wchar_p),
        ("pTo",                   ctypes.c_wchar_p),
        ("fFlags",                ctypes.c_ushort),
        ("fAnyOperationsAborted", _wt.BOOL),
        ("hNameMappings",         ctypes.c_void_p),
        ("lpszProgressTitle",     ctypes.c_wchar_p),
    ]


_FO_DELETE          = 0x0003
_FOF_ALLOWUNDO      = 0x0040   # sends to Recycle Bin (recoverable)
_FOF_NOCONFIRMATION = 0x0010
_FOF_SILENT         = 0x0004
_FOF_NOERRORUI      = 0x0400


def _recycle_one(path: str) -> Optional[str]:
    """Send a single path to the Windows Recycle Bin.

    Returns None on success, an error string on failure.
    Uses SHFileOperationW with FOF_ALLOWUNDO so the item is fully recoverable.
    """
    try:
        op = _SHFILEOPSTRUCTW()
        op.hwnd   = 0
        op.wFunc  = _FO_DELETE
        op.pFrom  = path + "\0"   # API requires double-null terminator
        op.fFlags = _FOF_ALLOWUNDO | _FOF_NOCONFIRMATION | _FOF_SILENT | _FOF_NOERRORUI

        ret = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        if ret != 0:
            return f"SHFileOperationW error {ret:#06x}"
        if op.fAnyOperationsAborted:
            return "Operation aborted by shell"
        return None
    except Exception as exc:
        return str(exc)


def _delete_one(path: str) -> Optional[str]:
    """Permanently delete a single file or folder. Returns error string or None."""
    try:
        if os.path.isfile(path) or os.path.islink(path):
            os.remove(path)
        elif os.path.isdir(path):
            shutil.rmtree(path)
        else:
            return "Path does not exist"
        return None
    except Exception as exc:
        return str(exc)


# ── Public API ───────────────────────────────────────────────────

def move_to_recycle_bin(paths: list) -> CleanupResult:
    """Move each path to the Windows Recycle Bin.

    Protected paths are silently skipped (counted in skipped_protected).
    Errors on individual paths do not abort the rest of the batch.
    """
    result = CleanupResult()
    for path in paths:
        if _is_protected_for_delete(path):
            result.skipped_protected.append(path)
            continue
        size = _get_size(path)
        err = _recycle_one(path)
        if err:
            result.errors_by_path[path] = err
            code = _extract_windows_error_code(err)
            if code is not None:
                result.error_codes_by_path[path] = code
            if _is_expected_in_use_error(err):
                result.in_use.append(path)
            else:
                result.failed.append(path)
        else:
            result.succeeded.append(path)
            result.total_bytes_freed += size
    return result


def permanent_delete(paths: list, perm_delete_enabled: bool) -> CleanupResult:
    """Permanently delete paths.

    Requires perm_delete_enabled=True (from settings).
    Raises ProtectedPathError immediately if any path is protected — does NOT skip them.
    """
    if not perm_delete_enabled:
        raise ValueError("Permanent delete is disabled in settings")
    for path in paths:
        if _is_protected_for_delete(path):
            raise ProtectedPathError(
                f"Protected path cannot be permanently deleted: {path}"
            )
    result = CleanupResult()
    for path in paths:
        size = _get_size(path)
        err = _delete_one(path)
        if err:
            result.errors_by_path[path] = err
            code = _extract_windows_error_code(err)
            if code is not None:
                result.error_codes_by_path[path] = code
            if _is_expected_in_use_error(err):
                result.in_use.append(path)
            else:
                result.failed.append(path)
        else:
            result.succeeded.append(path)
            result.total_bytes_freed += size
    return result


# ── Background worker ─────────────────────────────────────────────

class CleanupWorker(QThread):
    """Runs cleanup operations off the UI thread.

    Signals:
      progress(done, total, current_path) — emitted before each item
      log_line(str)                        — operator-feed messages
      finished(CleanupResult)              — emitted when all items processed
    """

    progress = Signal(int, int, str)   # done, total, path
    log_line = Signal(str)
    finished = Signal(object)          # CleanupResult

    MODE_RECYCLE   = "recycle_bin"
    MODE_PERMANENT = "permanent"

    def __init__(self, paths: list, mode: str = MODE_RECYCLE,
                 perm_delete_enabled: bool = False, parent=None):
        super().__init__(parent)
        self._paths = list(paths)
        self._mode = mode
        self._perm_delete_enabled = perm_delete_enabled
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        result = CleanupResult()
        total = len(self._paths)
        mode_label = "Recycle Bin" if self._mode == self.MODE_RECYCLE else "permanent delete"
        self.log_line.emit(f"[cleanup] moving {total} item(s) → {mode_label}...")

        for i, path in enumerate(self._paths):
            if self._cancel:
                self.log_line.emit("[cleanup] cancelled by user")
                break

            self.progress.emit(i, total, path)

            if _is_protected_for_delete(path):
                result.skipped_protected.append(path)
                self.log_line.emit(f"[cleanup] skipped (protected): {os.path.basename(path)}")
                continue

            size = _get_size(path)

            if self._mode == self.MODE_PERMANENT:
                if not self._perm_delete_enabled:
                    err = "Permanent delete disabled in settings"
                    result.failed.append(path)
                    result.errors_by_path[path] = err
                    continue
                err = _delete_one(path)
            else:
                err = _recycle_one(path)

            if err:
                result.errors_by_path[path] = err
                code = _extract_windows_error_code(err)
                if code is not None:
                    result.error_codes_by_path[path] = code
                if _is_expected_in_use_error(err):
                    result.in_use.append(path)
                    self.log_line.emit(
                        f"[cleanup] in use: {os.path.basename(path)} — still used by Windows or another app"
                    )
                else:
                    result.failed.append(path)
                    self.log_line.emit(
                        f"[cleanup] failed: {os.path.basename(path)} — {err}"
                    )
            else:
                result.succeeded.append(path)
                result.total_bytes_freed += size

        self.progress.emit(total, total, "")

        parts = [f"{len(result.succeeded)} succeeded"]
        if result.in_use:
            parts.append(f"{len(result.in_use)} in use")
        if result.failed:
            parts.append(f"{len(result.failed)} failed")
        if result.skipped_protected:
            parts.append(f"{len(result.skipped_protected)} protected skipped")
        self.log_line.emit(f"[cleanup] done: {', '.join(parts)}")

        self.finished.emit(result)
