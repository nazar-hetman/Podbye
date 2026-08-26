"""Carry a Vigil install's data across to Podbye, once, on first run.

The product was renamed after it had already shipped. Everything a person had
built up lived under the old name — settings, the Keep list of folders they
marked "never delete this", every saved scan session, the AI answer cache. A
rename that silently pointed at a new empty directory would present as total
data loss: no history, no Keep marks, the endpoint settings gone.

Two roots move, because the data was always split across both:

    %APPDATA%\\Vigil   -> %APPDATA%\\Podbye     config.json, sessions, logs
    %LOCALAPPDATA%\\Vigil -> %LOCALAPPDATA%\\Podbye   the AI explanation cache

Three properties this has to hold:

*Idempotent* — it runs on every start and does nothing at all once the old
directory is gone, which is the normal case forever after the first launch.

*Never destructive to newer data* — if a Podbye directory already exists, its
files win. A person who ran the new build, changed a setting, then restored an
old Vigil folder from a backup must not have that setting overwritten by the
older copy. Files present on both sides are left alone; only what Podbye does
not have is brought over.

*Never fatal* — a locked file, a permission error, a half-copied session: none
of that is a reason to refuse to start. The migration reports what it could not
move and the app carries on. Losing a cached AI answer is a nuisance; failing
to launch is not.

The old directory is left in place rather than deleted. It is the user's data,
this is a one-way rename they did not ask for, and a folder they can delete
themselves is a far better failure mode than one Podbye removed on their behalf.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

LEGACY_NAME = "Vigil"
CURRENT_NAME = "Podbye"

# Written into the new directory once the move is done, so a later run can tell
# "already migrated" from "nothing to migrate" when both folders exist.
_MARKER = ".migrated-from-vigil"


def _roots() -> list[tuple[Path, Path]]:
    """(old, new) directory pairs, for whichever AppData roots exist."""
    pairs = []
    for var in ("APPDATA", "LOCALAPPDATA"):
        base = os.environ.get(var, "")
        if base:
            pairs.append((Path(base) / LEGACY_NAME, Path(base) / CURRENT_NAME))
    if not pairs:
        home = Path.home()
        pairs = [(home / ".config" / LEGACY_NAME.lower(),
                  home / ".config" / CURRENT_NAME.lower()),
                 (home / ".cache" / LEGACY_NAME.lower(),
                  home / ".cache" / CURRENT_NAME.lower())]
    return pairs


def _merge(old: Path, new: Path, failures: list[str]) -> int:
    """Copy everything under *old* that *new* does not already have.

    Depth-first by walk, comparing per file rather than per directory: a
    partially populated Podbye folder (the user launched the new build once
    before restoring a backup) must still receive the sessions it is missing.
    """
    moved = 0
    for dirpath, _dirnames, filenames in os.walk(old):
        rel = Path(dirpath).relative_to(old)
        target_dir = new / rel
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            failures.append(f"{target_dir}: {exc}")
            continue
        for name in filenames:
            source, target = Path(dirpath) / name, target_dir / name
            if target.exists():
                continue          # newer data wins, always
            try:
                shutil.copy2(source, target)
                moved += 1
            except OSError as exc:
                failures.append(f"{source}: {exc}")
    return moved


def migrate(log_fn=None) -> dict:
    """Move any Vigil-era data into the Podbye directories.

    Returns a summary: {"moved": int, "failures": [str], "roots": [str]}.
    Safe to call on every start; a no-op when there is nothing to carry over.
    """
    def log(message: str):
        if log_fn:
            log_fn(message)

    moved, failures, roots = 0, [], []
    for old, new in _roots():
        if not old.is_dir() or old == new:
            continue
        if (new / _MARKER).exists() and not any(old.iterdir()):
            continue
        log(f"[migrate] carrying {old} over to {new}")
        count = _merge(old, new, failures)
        moved += count
        roots.append(str(old))
        try:
            (new / _MARKER).write_text(str(old), encoding="utf-8")
        except OSError:
            pass                   # the marker is an optimisation, not a lock
        log(f"[migrate]   -> {count} file(s) carried over")

    if failures:
        log(f"[migrate] {len(failures)} file(s) could not be copied; "
            f"the originals are still in place")
    return {"moved": moved, "failures": failures, "roots": roots}
