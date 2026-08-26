"""Paths the user has marked **Keep** — never selected, never deleted.

Asked for directly: *"I need irizi focus — I'm adding it to protected state. If
I click select all, it won't be selected."*

Podbye already has a Protected tier, but that one is Podbye's own judgement about
system-critical locations, and it is recomputed from scratch by every scan. This
is the other thing: a standing instruction from the user about their own files,
which has to outlive the scan that was on screen when they gave it.

Three properties follow from that:

* **It is config, not session state.** Kept paths live in ``config.json``
  beside the other settings, so they survive a rescan, a restart, and clearing
  the session store.
* **It covers the subtree.** People keep a *project*, not a file list. Marking
  ``E:/Irizi Focus`` keeps everything under it, including entities that did not
  exist when the mark was made.
* **It is enforced at the bottom, not the top.** The UI hides the buttons and
  skips the row in select-all, and ``cleanup_engine`` refuses the path anyway —
  the same belt-and-braces shape as the protected-path guard, so a stale entity
  from a session opened out of History cannot route around it.

Read through :func:`is_kept`; written through :func:`keep` / :func:`unkeep`.
Both go straight to the settings file so a mark made on the Findings screen is
in force for a cleanup started from anywhere else.
"""
from __future__ import annotations

import os
import threading

# A Keep mark covers everything beneath it, so a path too broad would silence
# the whole app. Reuses the judgement self_paths already makes about what is
# too big to be claimed as one thing.
from app.services.self_paths import _is_usable_root

_lock = threading.Lock()
_store = None                      # SettingsStore, injected at startup
_SETTING = "kept_paths"


def _norm(path: str) -> str:
    return (path or "").replace("\\", "/").rstrip("/").lower()


def set_store(store) -> None:
    """Point the keep list at the application's settings store."""
    global _store
    with _lock:
        _store = store


def reset_for_tests() -> None:
    """Forget the injected store, so the next read picks up a fresh APPDATA."""
    global _store
    with _lock:
        _store = None


def _ensure_store():
    """The injected store, or one of our own.

    ``main`` injects the application's store at startup so a mark made in the
    UI is the same object every screen reads. The fallback exists for the
    layer that has no UI at all: cleanup_engine enforces the keep list as a
    backstop, and a backstop that silently does nothing when nobody wired it
    up is not one.
    """
    global _store
    if _store is None:
        from app.config.settings_store import SettingsStore
        _store = SettingsStore()
    return _store


def _read() -> list[str]:
    """The stored paths, as the user gave them.

    Kept as typed rather than normalised: this list is shown back to them in
    Settings, and "E:/Irizi Focus" turning into "e:/irizi focus" reads as
    Podbye having mangled something. Comparison normalises both sides instead.
    """
    raw = _ensure_store().get(_SETTING, []) or []
    if not isinstance(raw, list):
        return []
    return [p.strip() for p in raw if isinstance(p, str) and p.strip()]


def _write(paths: list[str]) -> None:
    _ensure_store().set_and_save(_SETTING, paths)


def kept_paths() -> tuple[str, ...]:
    """Every path the user is keeping, as given, in the order marked."""
    with _lock:
        return tuple(_read())


def can_keep(path: str) -> bool:
    """False for a path too broad to be one thing a person keeps.

    A whole drive, or a shared dump folder like Downloads: keeping either
    would quietly take most of the disk out of Podbye's reach, and the user
    would have no way to tell why nothing is ever selectable.
    """
    norm = _norm(path)
    if not norm:
        return False
    return _is_usable_root(norm)


def is_kept(path: str) -> bool:
    """True when *path* is kept, or sits inside something that is."""
    norm = _norm(path)
    if not norm:
        return False
    for kept in kept_paths():
        root = _norm(kept)
        if norm == root or norm.startswith(root + "/"):
            return True
    return False


def kept_root_for(path: str) -> str:
    """The kept path that covers *path*, or "" — for explaining the block.

    "Kept" on a row the user never marked is confusing unless it can name the
    folder the mark is actually on.
    """
    norm = _norm(path)
    best = ""
    for kept in kept_paths():
        root = _norm(kept)
        if (norm == root or norm.startswith(root + "/")) and len(root) > len(best):
            best = kept
    return best


def keep(path: str) -> bool:
    """Start keeping *path*. Returns False when it is not a path we may keep."""
    if not can_keep(path):
        return False
    norm = _norm(path)
    given = (path or "").strip().rstrip("/\\")
    with _lock:
        paths = _read()
        if any(_norm(p) == norm for p in paths):
            return True
        # Marking a parent supersedes anything already kept inside it, so the
        # list stays the set of roots the user actually chose.
        paths = [p for p in paths if not _norm(p).startswith(norm + "/")]
        paths.append(given)
        _write(paths)
    return True


def unkeep(path: str) -> bool:
    """Stop keeping *path*. Returns False when it was not kept in its own right."""
    norm = _norm(path)
    with _lock:
        paths = _read()
        if not any(_norm(p) == norm for p in paths):
            return False
        _write([p for p in paths if _norm(p) != norm])
    return True


def display_name(path: str) -> str:
    """Leaf name for a kept path, for listing it back to the user."""
    leaf = os.path.basename((path or "").replace("\\", "/").rstrip("/"))
    return leaf or path
