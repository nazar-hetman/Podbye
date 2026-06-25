"""Quick Cleanup detector — locates known safe reclaimable locations.

Scans five categories in a background thread:
  1. User Temp    — contents of %TEMP% / %LOCALAPPDATA%\Temp
  2. Browser Cache — Chrome, Edge, Brave, Firefox, Opera, Vivaldi cache dirs
  3. Thumbnail Cache — thumbcache_*.db files in Windows Explorer folder
  4. Windows Update — contents of SoftwareDistribution\Download
  5. Windows Temp — contents of C:\Windows\Temp (may be restricted)

Each scanner returns a QuickCleanupCategory or None if nothing reclaimable
was found. Categories with zero bytes are suppressed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from PySide6.QtCore import QThread, Signal

from app.models.finding import _format_size


# ── Data model ────────────────────────────────────────────────────

@dataclass
class QuickCleanupCategory:
    key: str            # unique identifier
    label: str          # display name
    subtitle: str       # path hint shown in the row
    paths: list         # filesystem paths passed to CleanupWorker
    size_bytes: int
    file_count: int
    risk: str = "Safe"

    @property
    def size_display(self) -> str:
        return _format_size(self.size_bytes)


# ── Measurement helpers ───────────────────────────────────────────

def _measure_dir(path: str) -> tuple:
    """Return (total_bytes, file_count) for a directory tree. Best-effort."""
    total, count = 0, 0
    try:
        for root, _, files in os.walk(path):
            for fn in files:
                try:
                    total += os.path.getsize(os.path.join(root, fn))
                    count += 1
                except OSError:
                    pass
    except OSError:
        pass
    return total, count


def _measure_path(path: str) -> tuple:
    """Measure a single file or directory. Returns (bytes, count)."""
    try:
        if os.path.isfile(path):
            return os.path.getsize(path), 1
        if os.path.isdir(path):
            return _measure_dir(path)
    except OSError:
        pass
    return 0, 0


def _enum_top_level(folder: str) -> list:
    """Return absolute paths of all top-level entries inside folder."""
    result = []
    try:
        with os.scandir(folder) as it:
            for entry in it:
                result.append(entry.path)
    except (OSError, PermissionError):
        pass
    return result


# ── Category scanners ─────────────────────────────────────────────

def _scan_user_temp() -> Optional[QuickCleanupCategory]:
    """Enumerate top-level items inside %TEMP% and %LOCALAPPDATA%\Temp."""
    dirs: set = set()
    for var in ("TEMP", "TMP"):
        p = os.environ.get(var, "")
        if p:
            dirs.add(os.path.normpath(p))
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        dirs.add(os.path.normpath(os.path.join(local, "Temp")))

    paths: list = []
    total, count = 0, 0
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for entry in _enum_top_level(d):
            sz, fc = _measure_path(entry)
            paths.append(entry)
            total += sz
            count += fc

    if not paths:
        return None

    subtitle = "; ".join(sorted(d for d in dirs if os.path.isdir(d))[:2])
    return QuickCleanupCategory(
        key="user_temp",
        label="Temp Files",
        subtitle=subtitle,
        paths=paths,
        size_bytes=total,
        file_count=count,
    )


def _scan_windows_temp() -> Optional[QuickCleanupCategory]:
    """Enumerate top-level items inside C:\\Windows\\Temp."""
    sysroot = os.environ.get("SystemRoot", r"C:\Windows")
    wintemp = os.path.join(sysroot, "Temp")
    entries = _enum_top_level(wintemp)
    if not entries:
        return None

    paths: list = []
    total, count = 0, 0
    for e in entries:
        sz, fc = _measure_path(e)
        paths.append(e)
        total += sz
        count += fc

    if total == 0 and count == 0:
        return None

    return QuickCleanupCategory(
        key="windows_temp",
        label="Windows Temp",
        subtitle=wintemp,
        paths=paths,
        size_bytes=total,
        file_count=count,
    )


# Each tuple: (base_env_path, cache_subdir_name)
# The base is a "User Data"-style directory that may contain profile subdirs.
_BROWSER_CACHE_SPECS: list = [
    (r"%LOCALAPPDATA%\Google\Chrome\User Data",             "Cache"),
    (r"%LOCALAPPDATA%\Google\Chrome\User Data",             "Code Cache"),
    (r"%LOCALAPPDATA%\Google\Chrome\User Data",             "GPUCache"),
    (r"%LOCALAPPDATA%\Microsoft\Edge\User Data",            "Cache"),
    (r"%LOCALAPPDATA%\Microsoft\Edge\User Data",            "Code Cache"),
    (r"%LOCALAPPDATA%\Microsoft\Edge\User Data",            "GPUCache"),
    (r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\User Data", "Cache"),
    (r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\User Data", "Code Cache"),
    (r"%LOCALAPPDATA%\Mozilla\Firefox\Profiles",            "cache2"),
    (r"%APPDATA%\Opera Software\Opera Stable",              "Cache"),
    (r"%APPDATA%\Opera Software\Opera GX Stable",           "Cache"),
    (r"%LOCALAPPDATA%\Vivaldi\User Data",                   "Cache"),
]

_BROWSER_LABEL_MAP = {
    "chrome":   "Chrome",
    "edge":     "Edge",
    "brave":    "Brave",
    "firefox":  "Firefox",
    "opera":    "Opera",
    "vivaldi":  "Vivaldi",
}


def _scan_browser_cache() -> Optional[QuickCleanupCategory]:
    """Collect cache directories for all installed Chromium/Firefox browsers."""
    cache_dirs: list = []
    browsers: set = set()

    for base_pattern, sub in _BROWSER_CACHE_SPECS:
        base = os.path.expandvars(base_pattern)
        if not os.path.isdir(base):
            continue
        try:
            with os.scandir(base) as it:
                for entry in it:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                    candidate = os.path.join(entry.path, sub)
                    if os.path.isdir(candidate):
                        cache_dirs.append(candidate)
                        lo = candidate.lower()
                        for key, name in _BROWSER_LABEL_MAP.items():
                            if key in lo:
                                browsers.add(name)
                                break
        except OSError:
            continue

    if not cache_dirs:
        return None

    paths: list = []
    total, count = 0, 0
    for d in cache_dirs:
        sz, fc = _measure_dir(d)
        if sz > 0 or fc > 0:
            paths.append(d)
            total += sz
            count += fc

    if not paths:
        return None

    subtitle = " · ".join(sorted(browsers)) if browsers else "Browsers"
    return QuickCleanupCategory(
        key="browser_cache",
        label="Browser Cache",
        subtitle=subtitle,
        paths=paths,
        size_bytes=total,
        file_count=count,
    )


def _scan_thumbnail_cache() -> Optional[QuickCleanupCategory]:
    """Find thumbcache_*.db files in the Windows Explorer folder."""
    local = os.environ.get("LOCALAPPDATA", "")
    if not local:
        return None
    explorer = os.path.join(local, "Microsoft", "Windows", "Explorer")
    if not os.path.isdir(explorer):
        return None

    files: list = []
    total = 0
    try:
        for name in os.listdir(explorer):
            lo = name.lower()
            if lo.startswith("thumbcache_") and lo.endswith(".db"):
                fp = os.path.join(explorer, name)
                try:
                    sz = os.path.getsize(fp)
                    files.append(fp)
                    total += sz
                except OSError:
                    pass
    except OSError:
        return None

    if not files:
        return None

    return QuickCleanupCategory(
        key="thumbnail_cache",
        label="Thumbnail Cache",
        subtitle=explorer,
        paths=files,
        size_bytes=total,
        file_count=len(files),
    )


def _scan_windows_update() -> Optional[QuickCleanupCategory]:
    """Enumerate top-level items in SoftwareDistribution\\Download."""
    sysroot = os.environ.get("SystemRoot", r"C:\Windows")
    download = os.path.join(sysroot, "SoftwareDistribution", "Download")
    entries = _enum_top_level(download)
    if not entries:
        return None

    paths: list = []
    total, count = 0, 0
    for e in entries:
        sz, fc = _measure_path(e)
        paths.append(e)
        total += sz
        count += fc

    if total == 0 and count == 0:
        return None

    return QuickCleanupCategory(
        key="windows_update",
        label="Windows Update Cache",
        subtitle=download,
        paths=paths,
        size_bytes=total,
        file_count=count,
    )


# ── Detector thread ───────────────────────────────────────────────

_SCANNERS = [
    _scan_user_temp,
    _scan_browser_cache,
    _scan_thumbnail_cache,
    _scan_windows_update,
    _scan_windows_temp,
]


class QuickCleanupDetector(QThread):
    """Scans all quick cleanup categories in a background thread.

    Signals:
        category_found(QuickCleanupCategory) — one per non-empty category found
        finished()                            — all scanners completed
    """

    category_found = Signal(object)
    finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        for scanner in _SCANNERS:
            if self._cancel:
                break
            try:
                cat = scanner()
                if cat is not None:
                    self.category_found.emit(cat)
            except Exception:
                pass
        self.finished.emit()
