"""Drive / volume awareness — thin helpers over psutil with a stdlib fallback.

psutil is an optional dependency. When it is missing, capacity readouts still
work via the standard library (``shutil.disk_usage``) and the volume-isolation
check still works via ``os.stat().st_dev``; only the drive *type* label
(Fixed / Removable / Network / Optical) degrades to "Unknown".
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass

try:
    import psutil  # type: ignore
    HAVE_PSUTIL = True
except Exception:  # pragma: no cover - only when psutil is not installed
    psutil = None  # type: ignore
    HAVE_PSUTIL = False


@dataclass
class DriveInfo:
    """Capacity + type summary for one mounted volume."""

    mountpoint: str          # e.g. "C:\\"
    device: str              # e.g. "C:\\"
    fstype: str              # e.g. "NTFS"
    kind: str                # Fixed | Removable | Network | Optical | Unknown
    total: int               # bytes
    used: int                # bytes
    free: int                # bytes

    @property
    def percent_used(self) -> float:
        return (self.used / self.total * 100.0) if self.total else 0.0


def _classify(opts: str, fstype: str) -> str:
    """Map a psutil partition's opts/fstype to a human drive-type label."""
    o = (opts or "").lower()
    if "cdrom" in o or (fstype or "").lower() in {"cdfs", "udf"}:
        return "Optical"
    if "removable" in o:
        return "Removable"
    if "remote" in o or "network" in o:
        return "Network"
    if "fixed" in o:
        return "Fixed"
    return "Unknown"


def disk_usage(path: str) -> tuple[int, int, int]:
    """(total, used, free) bytes for the volume holding *path*. Stdlib-only."""
    try:
        u = shutil.disk_usage(path)
        return u.total, u.used, u.free
    except OSError:
        return 0, 0, 0


def same_volume(a: str, b: str) -> bool:
    """True if both paths live on the same volume (compares st_dev).

    Fail-open: on a stat error this returns True so callers never prune a
    branch just because one stat failed.
    """
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return True


def _match_partition(path: str):
    """Return the psutil partition whose mountpoint best matches *path*."""
    if not HAVE_PSUTIL:
        return None
    try:
        target = os.path.abspath(path).lower()
        best, best_len = None, -1
        for part in psutil.disk_partitions(all=False):
            mp = part.mountpoint
            if target.startswith(mp.lower()) and len(mp) > best_len:
                best, best_len = part, len(mp)
        return best
    except Exception:
        return None


def drive_kind(path: str) -> str:
    """Best-effort drive type for the volume holding *path* ("Unknown" w/o psutil)."""
    part = _match_partition(path)
    return _classify(part.opts, part.fstype) if part is not None else "Unknown"


def summarize(path: str) -> DriveInfo | None:
    """Capacity + type summary for the volume holding *path*, or None if unknown."""
    total, used, free = disk_usage(path)
    if not total:
        return None
    part = _match_partition(path)
    if part is not None:
        mp, fstype, kind = part.mountpoint, part.fstype, _classify(part.opts, part.fstype)
    else:
        mp = (os.path.splitdrive(os.path.abspath(path))[0] + os.sep) or os.path.abspath(path)
        fstype, kind = "", "Unknown"
    return DriveInfo(mountpoint=mp, device=mp, fstype=fstype, kind=kind,
                     total=total, used=used, free=free)


def list_drives() -> list[DriveInfo]:
    """All mounted drives with type + capacity. Empty list when psutil is absent."""
    if not HAVE_PSUTIL:
        return []
    out: list[DriveInfo] = []
    try:
        for part in psutil.disk_partitions(all=False):
            total, used, free = disk_usage(part.mountpoint)
            if not total:
                continue
            out.append(DriveInfo(
                mountpoint=part.mountpoint, device=part.device,
                fstype=part.fstype, kind=_classify(part.opts, part.fstype),
                total=total, used=used, free=free,
            ))
    except Exception:
        pass
    return out
