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


class _Unwalkable(Exception):
    """A directory on the way to an exclusion could not be enumerated."""


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


# Below this a preservation notice is not worth a line of the panel. Measured
# on a real screen: miniconda3 said "8 findings inside are listed separately
# and will be kept (27 KB)" — 27 KB of __pycache__ inside 24.3 GB. True, and
# noise. A notice that fires for a rounding error teaches the reader to skip
# the one that matters, which is the case this sentence exists for.
#
# Relative and absolute together: 0.5% catches the big folders, the 10 MB
# floor stops a small finding from being talked about in kilobytes.
_MATERIAL_SHARE = 0.005
_MATERIAL_FLOOR = 10 * 1024 * 1024


def keeps_something_inside(entity: dict) -> bool:
    """True when a separately listed finding lives inside and stays put.

    Only when it is worth saying so. Something is always *technically* kept
    whenever excluded_paths is non-empty; this asks whether the amount would
    change what the reader does.
    """
    if not excluded_paths(entity):
        return False
    kept = contained_bytes(entity)
    if kept <= 0:
        return False
    own = own_bytes(entity)
    threshold = max(_MATERIAL_FLOOR, int(own * _MATERIAL_SHARE))
    return kept >= threshold


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
    root_norm = _norm(root)
    if not root_norm:
        return []

    # Strict descendants only, de-duplicated. An exclusion that is the root
    # itself, a parent of it, or somewhere else entirely is not something this
    # root can carve around: keeping it in `keep` would either delete nothing
    # or, worse, be silently ignored while the caller believed it was honoured.
    # The root appearing in its own exclusions means the finding owns nothing.
    prefix = root_norm + "/"
    keep = set()
    for candidate in (excluded or []):
        norm = _norm(candidate)
        if not norm:
            continue
        if norm == root_norm:
            return []                 # owns nothing: take nothing
        if norm.startswith(prefix):
            keep.add(norm)
    if not keep:
        return [root]

    # Fail closed: every exclusion must still be there to be carved around. If
    # one has vanished or cannot be reached, the shape of the tree is not what
    # this scope was computed against, and the safe answer is to take nothing
    # rather than to take a parent whose contents we can no longer account for.
    for excluded_path in keep:
        if not os.path.exists(excluded_path):
            return []

    def below(norm_path: str) -> bool:
        child_prefix = norm_path + "/"
        return any(k.startswith(child_prefix) for k in keep)

    def is_reparse(path: str) -> bool:
        """A junction, symlink or mount point.

        Never descended into and never taken: what lies under one is not
        inside this folder at all, and deleting through it reaches data the
        finding never measured and the user never saw.
        """
        try:
            return os.path.islink(path) or os.path.ismount(path)
        except OSError:
            return True               # cannot tell: assume the dangerous one

    def walk(current: str) -> list:
        norm = _norm(current)
        if norm in keep:
            return []                 # owned by another finding
        if is_reparse(current):
            return []
        if not below(norm):
            return [current]          # nothing of anyone else's below here
        try:
            children = os.listdir(current)
        except OSError:
            raise _Unwalkable(current)
        out = []
        for child in children:
            out.extend(walk(os.path.join(current, child).replace("\\", "/")))
        return out

    try:
        targets = walk(root)
    except _Unwalkable:
        # A directory on the path to an exclusion could not be enumerated, so
        # the carve-out cannot be proven. Refusing to delete is the cheap
        # failure; deleting a parent because a child could not be listed is
        # the one that loses data.
        return []

    # De-duplicate while keeping order, and never return a path twice under
    # two spellings.
    seen = set()
    unique = []
    for target in targets:
        norm = _norm(target)
        if norm in seen:
            continue
        seen.add(norm)
        unique.append(target)
    return unique
