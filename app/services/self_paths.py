"""Where Vigil itself lives — so it never offers to clean itself up.

Vigil stores its settings, scan sessions, logs and AI cache under
``%APPDATA%\\Vigil``. On a full C:/ scan those classify like anybody else's
data: ``logs`` reads as a log folder and ``cache`` as a cache folder, both Safe,
both recycle-able. Nothing stopped Vigil from proposing to delete its own
session store — the file holding the very results being displayed — while it was
running.

Two roots are recognised: the directory Vigil runs from, and its data directory.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _norm(path) -> str:
    return str(path).replace("\\", "/").rstrip("/").lower()


# A self root has to be a folder that belongs to Vigil alone. A portable build
# run straight out of C:/Users/<u>/Downloads would otherwise nominate Downloads
# as "Vigil's install directory" and protect the entire folder from cleanup.
_TOO_BROAD_NAMES = {
    "downloads", "download", "desktop", "documents", "my documents",
    "pictures", "videos", "music", "temp", "tmp", "users", "home",
    "program files", "program files (x86)", "programdata", "appdata",
    "local", "locallow", "roaming", "programs", "bin", "src", "app",
}


def _is_usable_root(norm: str) -> bool:
    """Reject drive roots and shared dump folders — see _TOO_BROAD_NAMES."""
    parts = [p for p in norm.split("/") if p]
    if len(parts) < 2:                      # "c:" — a whole drive
        return False
    return parts[-1] not in _TOO_BROAD_NAMES


def install_dir() -> str:
    """The directory Vigil runs from: the frozen exe's folder, or the source tree."""
    if getattr(sys, "frozen", False):
        return _norm(Path(sys.executable).resolve().parent)
    # app/services/self_paths.py → <project root>
    return _norm(Path(__file__).resolve().parents[2])


def data_dir() -> str:
    """%APPDATA%/Vigil — config.json, sessions, logs, AI cache."""
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        return _norm(Path(appdata) / "Vigil")
    return _norm(Path.home() / ".config" / "vigil")


def self_roots() -> tuple[str, ...]:
    """Normalised, de-duplicated roots that belong to Vigil.

    Computed per call rather than cached at import: the tests move APPDATA, and
    a frozen build resolves sys.executable only once it is actually frozen.
    """
    roots: list[str] = []
    for candidate in (install_dir(), data_dir()):
        if candidate and _is_usable_root(candidate) and candidate not in roots:
            roots.append(candidate)
    return tuple(roots)


def is_self_path(path: str) -> bool:
    """True when *path* is one of Vigil's own roots, or sits inside one."""
    if not path:
        return False
    norm = _norm(path)
    return any(norm == r or norm.startswith(r + "/") for r in self_roots())
