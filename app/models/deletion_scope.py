"""One number per finding: what it owns is what it removes.

Podbye already decided who owns each byte. ``_enforce_disjoint_sizes`` charges
every scanned byte to exactly one entity — when a nested entity survives as its
own row, its bytes belong to *it*, not to the folder around it. That is why a
category total is honest and why the storage map adds up to the disk.

Deletion was the one place that ignored that decision. A folder-backed entity
was handed whole to SHFileOperationW, which takes the subtree including rows
owned by something else. So the screen showed three numbers for one action:

    Chrome Data     5.4 GB     the row, and what Chrome owns
    CONTENTS        7.4 GB     the physical folder
    the button      7.4 GB     what actually went

Two ways to collapse that to one. Making everything inclusive would mean a
folder and the nested row inside it both claiming the same bytes, so category
totals — and the storage map's headline figure — would exceed the disk. The
other way is to make deletion obey the ownership model that already exists:

    **A finding removes what it owns, and nothing owned by another finding.**

That is what this module implements. Its consequences are worth stating:

* Every quantity on screen is the same quantity. ``size_bytes`` is the row, the
  contents total, the selection contribution and the deletion scope.
* A selection total is a plain sum again. Disjoint ownership means no
  double-counting is possible, so no union arithmetic is needed.
* Recycling a folder that contains a separately listed finding leaves that
  finding behind — on disk and in the list, where the user can act on it. The
  folder shell stays because something still lives in it. That is not a
  leftover; it is the other finding, which was never part of this one.

Nothing is stranded by this. Low-value entities are suppressed *before* the
disjointness pass, so every subtracted byte belongs to a finding that is
actually listed somewhere and can be removed on its own.
"""
from __future__ import annotations

import os


def _norm(path: str) -> str:
    return (path or "").replace("\\", "/").rstrip("/").lower()


def file_paths_of(entity: dict) -> list:
    """The files this entity stands for, if it stands for files."""
    return [p for p in (entity.get("removable_file_paths") or []) if p and p.strip()]


def is_folder_backed(entity: dict) -> bool:
    """True when recycling this entity acts on a folder rather than a list.

    A duplicate group carries its own per-copy paths and is handled by the
    cleanup dialog's duplicate branch, so it is never folder-backed here.
    """
    if entity.get("entity_type") == "duplicate_group":
        return False
    return not file_paths_of(entity)


def excluded_paths(entity: dict) -> list:
    """Nested findings inside this one, which it does not own and must not take."""
    if not is_folder_backed(entity):
        return []
    return [p for p in (entity.get("contained_paths") or []) if p and p.strip()]


def own_bytes(entity: dict) -> int:
    """What this finding owns — the row, the total, and the deletion scope."""
    return int(entity.get("size_bytes", 0) or 0)


def deletion_scope_bytes(entity: dict) -> int:
    """What removing this finding takes off the disk.

    The same as what it owns, with one exception the model has to allow for.
    A duplicate group's ``size_bytes`` is every copy, because that is what the
    group occupies — but cleanup keeps the newest and removes the rest. The
    row already showed the reclaimable figure while the selection total summed
    the whole group, so three identical 2 GB copies read as "4.0 GB" on the row
    and "6.0 GB" beside the button that removes 4.0 GB.
    """
    if entity.get("entity_type") == "duplicate_group":
        locations = entity.get("duplicate_locations") or []
        removable = {p for p in (entity.get("removable_duplicate_paths") or []) if p}
        if removable and locations:
            # Measured per copy, like the cleanup targets, rather than trusting
            # a stored total that a partial cleanup may have left behind.
            by_path = {loc.get("path"): int(loc.get("size_bytes", 0) or 0)
                       for loc in locations if isinstance(loc, dict)}
            if any(path in by_path for path in removable):
                return sum(by_path.get(path, 0) for path in removable)
        stored = int(entity.get("dup_reclaimable", 0) or 0)
        if stored:
            return stored
    return own_bytes(entity)


def deletion_scope_files(entity: dict) -> int:
    if not is_folder_backed(entity):
        return len(file_paths_of(entity)) or int(entity.get("file_count", 0) or 0)
    return int(entity.get("file_count", 0) or 0)


def keeps_something_inside(entity: dict) -> bool:
    """True when a separately listed finding lives inside and stays put."""
    return bool(excluded_paths(entity))


def contained_bytes(entity: dict) -> int:
    return int(entity.get("contained_bytes", 0) or 0)


def covers(entity: dict, path: str) -> bool:
    """True when recycling *entity* removes *path*.

    A file list covers exactly the paths it names — which is what stops a
    finding that owns part of a folder from taking its siblings. A folder
    covers its subtree *except* the nested findings it does not own.
    """
    target = _norm(path)
    if not target:
        return False
    if not is_folder_backed(entity):
        return target in {_norm(p) for p in file_paths_of(entity)}
    root = _norm(entity.get("path", ""))
    if not root or not (target == root or target.startswith(root + "/")):
        return False
    for other in excluded_paths(entity):
        keep = _norm(other)
        if target == keep or target.startswith(keep + "/"):
            return False
    return True


def union_scope_bytes(entities: list) -> int:
    """What a selection removes. A plain sum, because ownership is disjoint.

    This is the payoff of the model rather than an oversight: no finding can
    remove bytes charged to another, so no pair of selections can overlap and
    there is nothing to de-duplicate.
    """
    return sum(own_bytes(e) for e in entities)


def expand_targets(root: str, excluded: list) -> list:
    """The top-most paths under *root* that *root* actually owns.

    Descends only along the ancestors of an exclusion — every branch with
    nothing excluded below it is taken whole — so the cost is the depth of the
    exclusions, not the size of the tree. Chrome minus one nested cache is
    three listdir calls.

    Returns ``[root]`` when nothing is excluded, which is the ordinary case and
    keeps a plain folder a single shell operation.

    An unreadable directory yields nothing rather than its parent: refusing to
    delete is the safe failure, and deleting a parent because a child could
    not be listed is the unsafe one.
    """
    if not root:
        return []
    keep = {_norm(p) for p in (excluded or []) if p}
    if not keep:
        return [root]

    def below(norm_path: str) -> bool:
        prefix = norm_path + "/"
        return any(k.startswith(prefix) for k in keep)

    def walk(current: str) -> list:
        norm = _norm(current)
        if norm in keep:
            return []                 # owned by another finding
        if not below(norm):
            return [current]          # nothing of anyone else's below here
        try:
            children = os.listdir(current)
        except OSError:
            return []                 # cannot enumerate: take nothing
        out = []
        for child in children:
            out.extend(walk(os.path.join(current, child).replace("\\", "/")))
        return out

    return walk(root)
