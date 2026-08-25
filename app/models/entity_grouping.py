"""Group findings by the application or container that owns them.

A scan of one machine produced 1,241 rows, and Discord alone accounted for 22
of them — `shared_proto_db`, `Session Storage`, `Network`, `WidevineCdm`,
`Crashpad`, four separate `node_modules`… `AppData\\Local\\Packages` produced
120 rows, `Program Files\\dotnet` another 43. Those are not decisions a user
can make. "Discord — 1.2 GB" is.

So each entity gets an *owner*: the nearest ancestor directory that is a thing
a person would name. The rule is deliberately structural rather than a list of
known apps — it is the segment directly below a container root
(``AppData/Roaming``, ``Program Files``, …), because that is where Windows and
every installer put one folder per application.

Nothing is hidden here. Grouping decides what nests under what; the caller
still shows every row, and a group's own row carries the totals so a user who
wants to tidy 363 small files can act on them in one go instead of scrolling
past them one at a time.
"""
from __future__ import annotations

# Directories whose immediate children are one-per-application.
_APP_CONTAINERS = (
    "appdata/roaming",
    "appdata/local",
    "appdata/locallow",
    "program files",
    "program files (x86)",
    "programdata",
)

# Containers that are themselves *inside* a container: the app folder is one
# level deeper. AppData/Local/Packages/<pkg> is a Store app; AppData/Local/
# Programs/<app> is a per-user install; Program Files/WindowsApps/<pkg> the
# same for machine-wide Store installs.
_NESTED_CONTAINERS = (
    "appdata/local/packages",
    "appdata/local/programs",
    "appdata/locallow/packages",
    "program files/windowsapps",
)


def _norm(path: str) -> str:
    return (path or "").replace("\\", "/").rstrip("/")


# ── the authoritative signal ──────────────────────────────────────
# Windows already knows where most programs live: every entry under
# CurrentVersion\Uninstall carries a DisplayName and an InstallLocation. Vigil
# reads that table during detection but only used it defensively, to avoid
# creating entities inside an installed app. Used as the *grouping* authority
# it gives exact boundaries and the app's real name — "Visual Studio Code",
# not the "Code" its folder happens to be called — for every registered
# program, which no amount of path-shape guessing can match.


def build_app_index(installed: dict | None = None) -> dict[str, str]:
    """``{normalised install location: display name}`` for grouping.

    Pass *installed* to stay off the registry (tests, non-Windows); otherwise
    the detector's cached registry read is used.
    """
    if installed is None:
        try:
            from app.services.entity_detector import _get_installed_programs
            installed = _get_installed_programs()
        except Exception:
            installed = {}

    index: dict[str, str] = {}
    for norm_loc, info in (installed or {}).items():
        loc = _norm(norm_loc)
        if not loc or not _is_usable_install_location(loc):
            continue
        name = (info or {}).get("name") or ""
        if name:
            index[loc.lower()] = name
    return index


def _is_usable_install_location(loc: str) -> bool:
    """Reject install locations too broad to be one application.

    Installers routinely record a container rather than their own folder —
    ``C:\\Program Files`` or even ``C:\\`` — and honouring those would put every
    program on the machine into a single group.
    """
    lowered = loc.lower()
    segments = [s for s in lowered.split("/") if s]
    if len(segments) < 2:                  # "c:" or "c:/"
        return False
    tail = "/".join(segments[1:])
    if tail in _APP_CONTAINERS or tail in _NESTED_CONTAINERS:
        return False
    return True


def owner_key(path: str, app_index: dict[str, str] | None = None) -> str:
    """The app/container folder that owns *path*, or "" when it owns itself.

    Registry first, path shape second. Returns a normalised path; an entity
    whose own path equals its owner_key is the group's root and becomes the
    parent row rather than a child.
    """
    norm = _norm(path)
    lowered = norm.lower()

    # 1. Registry — longest matching InstallLocation wins, so a plugin folder
    #    under a suite goes to the specific product, not the suite.
    if app_index:
        best = ""
        for loc in app_index:
            if (lowered == loc or lowered.startswith(loc + "/")) and len(loc) > len(best):
                best = loc
        if best:
            return norm[:len(best)]

    # 2. Path shape. Deeper containers first, or AppData/Local would claim
    #    Packages/<pkg> for itself and every Store app would land in one bucket.
    for container in _NESTED_CONTAINERS + _APP_CONTAINERS:
        marker = "/" + container + "/"
        idx = lowered.find(marker)
        if idx == -1:
            continue
        rest = norm[idx + len(marker):]
        if not rest:
            return ""
        first = rest.split("/", 1)[0]
        return norm[:idx + len(marker)] + first
    return ""


_USER_DATA_CONTAINERS = ("appdata/roaming", "appdata/local", "appdata/locallow")


def merge_key(owner: str) -> str:
    """Key that unites one app's several data folders.

    An app routinely owns both ``AppData/Roaming/Discord`` and
    ``AppData/Local/Discord``; treating those as two apps gave the user two
    "Discord" rows to reason about. Anything under a per-user data container
    merges on the folder name, so both land in one group. Everywhere else the
    owner path itself is the key — two unrelated ``Program Files/Common``
    folders on different drives must not merge.
    """
    lowered = owner.lower()
    for container in _USER_DATA_CONTAINERS:
        marker = "/" + container + "/"
        idx = lowered.find(marker)
        if idx != -1:
            name = lowered[idx + len(marker):]
            # Only a direct child merges by name. Packages/<pkg> and
            # Programs/<app> keep their full path — the name alone
            # ("Packages") is not the app.
            if "/" not in name:
                return "app:" + name
    return lowered


def group_entities(entities: list[dict],
                   app_index: dict[str, str] | None = None) -> list[dict]:
    """Fold *entities* into owner groups, preserving the order given.

    Returns a list of ``{"root": entity|None, "owner": str, "name": str,
    "members": [...], "size_bytes": int, "file_count": int}``. Order follows
    the first appearance of each group in *entities*, so an already-sorted
    list stays sorted by whatever the caller sorted it by.

    An entity with no owner — or the only member of its group — comes back as
    a group of one, so the caller can render it exactly as it does today.
    """
    if app_index is None:
        app_index = build_app_index()

    groups: list[dict] = []
    by_owner: dict[str, dict] = {}

    for entity in entities:
        path = _norm(entity.get("path", ""))
        owner = owner_key(path, app_index)
        # An entity sitting AT the owner path is the group's root, not a
        # child of itself.
        is_root = bool(owner) and path.lower() == owner.lower()
        key = merge_key(owner) if owner else f"\0solo:{len(groups)}"

        group = by_owner.get(key)
        if group is None:
            group = {"root": None, "owner": owner, "members": [],
                     "name": app_index.get(owner.lower(), "") if owner else "",
                     "size_bytes": 0, "reclaimable_bytes": 0, "file_count": 0}
            by_owner[key] = group
            groups.append(group)

        if is_root and group["root"] is None:
            group["root"] = entity
        else:
            group["members"].append(entity)
        group["size_bytes"] += int(entity.get("size_bytes", 0) or 0)
        group["reclaimable_bytes"] += int(entity.get("reclaimable_bytes", 0) or 0)
        group["file_count"] += int(entity.get("file_count", 0) or 0)

    return groups


_LOCATION_HINTS = {"roaming", "local", "locallow", "shared", "programdata",
                   "x86", "64-bit", "program files", "program files (x86)",
                   "appdata", "app data"}


def _strip_location_hint(name: str, owner: str) -> str:
    """Drop a trailing "(Roaming)" / "(Local)" from a group's title.

    Those parentheses are added by the detector to tell two rows of the same
    app apart — and a group exists precisely because both rows are the same
    app. Naming the whole group "Microsoft (Roaming)" then states the group's
    *total* under the name of one of its halves. Reported as "roaming and
    local but no generals".

    Only a hint that names a location or a segment of the owner path is
    removed, so a folder genuinely called "Mario15 (GPS, 123 photos)" keeps
    its name.
    """
    if not name.endswith(")") or "(" not in name:
        return name
    head, _, tail = name.rpartition(" (")
    hint = tail[:-1].strip().lower()
    if not head.strip() or not hint:
        return name
    segments = {seg for seg in owner.lower().replace("\\", "/").split("/") if seg}
    if hint in _LOCATION_HINTS or hint in segments:
        return head.strip()
    return name


def group_label(group: dict) -> str:
    """Display name for a group's parent row.

    The registry's DisplayName wins when there is one — it is what the user
    installed and what Add/Remove Programs calls it, so "Visual Studio Code"
    rather than the folder name "Code".
    """
    if group.get("name"):
        return group["name"]
    owner = group.get("owner", "")
    root = group.get("root")
    if root and root.get("name"):
        return _strip_location_hint(root["name"], owner)
    return owner.rsplit("/", 1)[-1] if owner else ""


# Where an app keeps data, in the words Windows uses for it.
_CONTAINER_LABELS = (
    ("appdata/roaming", "Roaming"),
    ("appdata/locallow", "LocalLow"),
    ("appdata/local/packages", "Store app"),
    ("appdata/local/programs", "Programs"),
    ("appdata/local", "Local"),
    ("program files (x86)", "Program Files (x86)"),
    ("program files/windowsapps", "Store app"),
    ("program files", "Program Files"),
    ("programdata", "ProgramData"),
)


def location_label(path: str) -> str:
    """The Windows location *path* sits in — "Roaming", "Program Files", …

    Returns "" for anywhere else, where the path itself is the better answer.
    """
    lowered = _norm(path).lower()
    for marker, label in _CONTAINER_LABELS:
        if f"/{marker}/" in lowered + "/":
            return label
    return ""


def group_locations(group: dict) -> list[str]:
    """The distinct places a group's members live, in first-seen order.

    A group's whole claim is that these rows are one app; this is the part of
    that claim a user can check. Empty when nothing in the group sits in a
    recognised Windows location.
    """
    seen: list[str] = []
    members = list(group.get("members") or [])
    if group.get("root") is not None:
        members.insert(0, group["root"])
    for entity in members:
        label = location_label(entity.get("path", ""))
        if label and label not in seen:
            seen.append(label)
    return seen
