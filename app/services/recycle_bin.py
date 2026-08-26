"""Query and empty the Windows Recycle Bin.

Podbye deliberately never deletes permanently — every cleanup is a *move* to
the Recycle Bin, so nothing it does is irreversible. The catch is that moving
a file to the Recycle Bin on the same volume is a rename: **not one byte of
disk space is freed until the bin is emptied.**

Nobody was told that. On one real machine the bin held 16.7 GB across 795
items while the user kept running Quick Cleanup and wondering why "a couple of
GB can't be deleted" — the space had been reclaimed over and over and was
sitting in `$Recycle.Bin` the whole time.

Emptying stays the user's own explicit decision, which is why this module only
reports by default. But the number has to be on screen, or the app's central
promise — "you got space back" — is not true.
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
