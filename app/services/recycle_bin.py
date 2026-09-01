"""Query and empty the Windows Recycle Bin.

Podbye sends cleanup to the Recycle Bin by default. Moving a file to the
Recycle Bin on the same volume is a rename: **not one byte of disk space is
freed until the bin is emptied.** Emptying the bin is irreversible.

The Recycle Bin state must be visible: otherwise users can repeat Quick Cleanup
without realizing that the space is still sitting in `$Recycle.Bin`.

Emptying stays the user's own explicit decision, which is why this module only
reports by default.
"""
from __future__ import annotations

import ctypes


class _SHQUERYRBINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("i64Size", ctypes.c_longlong),
        ("i64NumItems", ctypes.c_longlong),
    ]


# SHEmptyRecycleBin flags: no confirmation dialog, no progress UI, no sound.
# Podbye asks for confirmation itself, in its own language and styling.
SHERB_NOCONFIRMATION = 0x01
SHERB_NOPROGRESSUI = 0x02
SHERB_NOSOUND = 0x04


def recycle_bin_status(drive: str | None = None) -> tuple[int, int]:
    """``(bytes, item_count)`` currently held in the Recycle Bin.

    *drive* limits the query to one volume (e.g. ``"C:\\"``); None covers all.
    Returns ``(0, 0)`` when the API is unavailable — a missing number must
    never be reported as "you have space to reclaim".
    """
    info = _SHQUERYRBINFO()
    info.cbSize = ctypes.sizeof(info)
    try:
        result = ctypes.windll.shell32.SHQueryRecycleBinW(drive, ctypes.byref(info))
    except (AttributeError, OSError):
        return 0, 0
    if result != 0:                      # S_OK is 0
        return 0, 0
    return int(info.i64Size), int(info.i64NumItems)


def empty_recycle_bin(drive: str | None = None) -> tuple[bool, str]:
    """Empty the bin. Returns ``(ok, message)``.

    This is the one genuinely irreversible thing Podbye can do, so it is never
    called without the user having said yes to a dialog that says so.
    """
    from app.i18n import tr

    flags = SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND
    try:
        result = ctypes.windll.shell32.SHEmptyRecycleBinW(None, drive, flags)
    except (AttributeError, OSError) as exc:
        return False, tr("Could not empty the Recycle Bin: {error}", error=str(exc))
    # S_OK, or "already empty" — both mean there is nothing left to do.
    if result in (0, 0x8003_0000 - 0x100000000):
        return True, tr("Recycle Bin emptied.")
    if result == 0:
        return True, tr("Recycle Bin emptied.")
    return False, tr("Windows could not empty the Recycle Bin (error {code}).",
                     code=result)


# ── Will this item actually reach the bin? ────────────────────────
#
# SHFileOperationW is passed FOF_NOCONFIRMATION, and Windows answers a request
# it cannot satisfy by destroying the file instead: an item larger than the
# volume's Recycle Bin quota is deleted outright, and the call still reports
# success. A volume with NukeOnDelete set does the same to everything on it.
#
# Detecting that afterwards — which is what Podbye used to do — is too late.
# The quota is per-volume and is in the registry, so it can be read before
# anything is touched, and the item skipped instead.

_BITBUCKET = r"Software\Microsoft\Windows\CurrentVersion\Explorer\BitBucket\Volume"


class RecyclePolicy:
    """What the Recycle Bin will do with an item on one volume.

    ``max_bytes`` and ``nuke_on_delete`` are None when the policy could not be
    read. Unknown is not the same as unlimited, and callers are expected to
    say so rather than assume the item is safe.
    """

    __slots__ = ("nuke_on_delete", "max_bytes")

    def __init__(self, nuke_on_delete=None, max_bytes=None):
        self.nuke_on_delete = nuke_on_delete
        self.max_bytes = max_bytes

    @property
    def known(self) -> bool:
        return self.nuke_on_delete is not None or self.max_bytes is not None

    def refuses(self, size_bytes: int) -> str:
        """Why an item of *size_bytes* would not reach the bin, or ""."""
        if self.nuke_on_delete:
            return "bin_disabled"
        if self.max_bytes is not None and size_bytes > self.max_bytes:
            return "too_large"
        return ""


def _volume_guid(path: str) -> str:
    """``{guid}`` of the volume holding *path*, or ""."""
    import os

    drive = os.path.splitdrive(os.path.abspath(path))[0]
    if not drive:
        return ""
    buf = ctypes.create_unicode_buffer(64)
    try:
        ok = ctypes.windll.kernel32.GetVolumeNameForVolumeMountPointW(
            ctypes.c_wchar_p(drive + "\\"), buf, ctypes.sizeof(buf))
    except Exception:
        return ""
    if not ok:
        return ""
    name = buf.value or ""          # \?\Volume{guid}\
    start, end = name.find("{"), name.find("}")
    return name[start:end + 1] if start != -1 and end != -1 else ""


def recycle_bin_policy(path: str) -> RecyclePolicy:
    """Read the Recycle Bin settings for the volume holding *path*.

    Returns an all-unknown policy rather than raising: a machine where this
    cannot be read must still be able to clean up, and the caller decides what
    an unknown policy means.
    """
    guid = _volume_guid(path)
    if not guid:
        return RecyclePolicy()
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _BITBUCKET + "\\" + guid) as key:
            nuke = max_mb = None
            try:
                nuke = bool(winreg.QueryValueEx(key, "NukeOnDelete")[0])
            except OSError:
                pass
            try:
                max_mb = int(winreg.QueryValueEx(key, "MaxCapacity")[0])
            except OSError:
                pass
    except OSError:
        return RecyclePolicy()
    # MaxCapacity is in megabytes; -1 means "let Windows manage it", which is
    # a size we cannot predict, so it stays unknown rather than becoming a
    # nonsensical negative limit.
    max_bytes = max_mb * 1024 * 1024 if max_mb is not None and max_mb >= 0 else None
    return RecyclePolicy(nuke_on_delete=nuke, max_bytes=max_bytes)
