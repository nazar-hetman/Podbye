"""What is inside an entity, in words a person can act on.

The inspector used to answer "what am I deleting?" with a property table and a
separate Files tab, so the contents of a 160 GB folder were one click away from
a delete button and most people never looked. This module produces the answer
that goes *on* the main view.

Three things it is deliberately not:

* **Not a directory tree.** "Steam → steamapps → common → …" is the filesystem
  restating itself. What a user needs is "Installed games — 148 GB".
* **Not exhaustive.** Podbye is a cleanup tool, not a file browser. The section
  names what dominates and rolls the tail into one row.
* **Not free.** Measured on the reporting machine, a full walk of Steam's
  40,349 files takes ~390 ms warm and `E:/My Projects` does not finish inside
  400 ms at all. Everything here is budgeted and says so when it is cut short.

Two kinds of entity, decided by something already on the dict:

* one that stands for a **list of files** (a loose bucket, an installer group)
  — its ``removable_file_paths`` are the contents, and each is separately
  removable;
* one that stands for a **folder** — its contents are components, and they go
  with it whether the user likes it or not.

A single file, or a folder with nothing inside worth naming, gets no section.
Showing "Steam contains Steam" is what the redesign set out to remove.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

# ── modes ─────────────────────────────────────────────────────────

MODE_NONE = "none"          # nothing worth saying
MODE_FILES = "files"        # independently removable items
MODE_CONTENTS = "contents"  # parts of one indivisible thing
MODE_ITEMS = "items"        # things that live inside, each its own decision


@dataclass
class ContentRow:
    """One line of the contents section."""

    label: str
    size_bytes: int = 0
    path: str = ""
    # A rule named this row, so it is a concept ("Installed games") rather
    # than a folder that happens to be there. Worth showing first.
    named: bool = False
    file_count: int = 0

    @property
    def is_other(self) -> bool:
        return not self.path and not self.named


@dataclass
class Contents:
    """The contents section for one entity."""

    mode: str = MODE_NONE
    rows: list = field(default_factory=list)
    total_bytes: int = 0
    total_files: int = 0
    # The walk hit its time budget, so the rows are a partial view and the
    # section has to say so rather than quietly under-report a folder.
    truncated: bool = False
    # True while only the free, no-I/O summary is available.
    provisional: bool = False

    def __bool__(self) -> bool:
        return self.mode != MODE_NONE and bool(self.rows)


# ── which mode ────────────────────────────────────────────────────

def file_paths_of(entity: dict) -> list:
    """The files this entity stands for: no blanks, no repeats, in order.

    Normalising here rather than at each reader is what keeps the section, the
    count and the cleanup plan agreeing with each other \u2014 a path listed twice
    would be counted twice, shown twice, and attempted twice.
    """
    seen = {}
    for raw in (entity.get("removable_file_paths") or []):
        if not isinstance(raw, str):
            continue
        path = raw.strip()
        if path:
            seen.setdefault(path.replace("\\", "/").lower(), path)
    return list(seen.values())


def mode_for(entity: dict) -> str:
    """Which representation this entity's contents deserve.

    A file list of one is not a list — pass 8 already turns a bucket of one
    into the file itself, and a section repeating the row's own name adds
    nothing.
    """
    paths = file_paths_of(entity)
    if len(paths) >= 2:
        return MODE_FILES
    if len(paths) == 1:
        return MODE_NONE
    if int(entity.get("folder_count", 0) or 0) <= 0 \
            and int(entity.get("file_count", 0) or 0) <= 1:
        return MODE_NONE
    return MODE_CONTENTS


def _norm(path: str) -> str:
    """Comparison form for a path: forward slashes, no trailing one, folded."""
    return (path or "").replace("\\", "/").rstrip("/").lower()


# ── items: entities that live inside another entity ────────────

# How far below a finding a child may sit and still be one of its *items*.
# Measured on a real E:/ scan: "Projects" contains a colmap run's database.db
# eight levels down. That is not an item of Projects in any sense a person
# means it — it is a file inside a project inside the workspace — and listing
# it would be the folder-by-folder navigation this is meant to avoid.
MAX_ITEM_DEPTH = 3


def child_entities(entity: dict, everything: list) -> list:
    """The entities that live directly inside *entity*.

    "Directly" in entity terms, not path terms: a child whose nearest
    entity-ancestor is something else belongs to that one instead, so a
    project's cache is an item of the project rather than of the workspace
    two levels up.

    Measured on a real E:/ scan, 57 of 137 entities live inside another one
    and every one of them is shown in the findings list as a sibling of its
    own container. This is what the inspector uses to say so.
    """
    root = _norm(entity.get("path", ""))
    if not root:
        return []

    inside = []
    for other in everything:
        path = _norm(other.get("path", ""))
        if not path or path == root or not path.startswith(root + "/"):
            continue
        # A bucket's path is the folder its files sit in, not a subtree it
        # owns; two buckets in one folder are not each other's children.
        if other.get("removable_file_paths"):
            inside.append((path, other))
            continue
        inside.append((path, other))

    kept = []
    for path, other in inside:
        nearer = any(
            p != path and path.startswith(p + "/") and not o.get("removable_file_paths")
            for p, o in inside
        )
        if nearer:
            continue
        if path[len(root) + 1:].count("/") + 1 > MAX_ITEM_DEPTH:
            continue
        kept.append(other)
    kept.sort(key=lambda e: -int(e.get("size_bytes", 0) or 0))
    return kept


def items_summary(entity: dict, everything: list, exclude=None) -> Contents:
    """The ITEMS section: what lives inside, each its own decision.

    Its own total, deliberately. A parent's size has its children subtracted
    by the disjointness pass — ``_src`` displays 2.4 GB while the two projects
    inside it hold 30 GB — so borrowing the header's figure would be wrong in
    both directions.

    *exclude* drops children the caller is already showing somewhere else. An
    entity's children are frequently members of the same group as the entity
    itself, so the panel that lists a group's parts and this section were
    listing the same rows — same names, same sizes, one above the other. What
    is left is what the parts list does not already say.
    """
    children = child_entities(entity, everything)
    if exclude:
        skip = {_norm(p) for p in exclude if p}
        children = [c for c in children if _norm(c.get("path", "")) not in skip]
    if not children:
        return Contents()
    rows = [ContentRow(label=child.get("name", ""),
                       size_bytes=int(child.get("size_bytes", 0) or 0),
                       path=child.get("path", ""), named=True,
                       file_count=int(child.get("file_count", 0) or 0))
            for child in children]
    return Contents(mode=MODE_ITEMS, rows=rows,
                    total_bytes=sum(r.size_bytes for r in rows),
                    total_files=sum(r.file_count for r in rows))


# ── the free summary, before any disk is touched ──────────────────

def quick_summary(entity: dict) -> Contents:
    """What can be said with no I/O at all.

    Every entity carries ``children_sample`` — up to 15 names, in scandir
    order, without sizes. For a collection that is genuinely useful ("Models"
    leads with checkpoints, loras, vae); for Steam it is alphabetical noise
    (".cef-dev-tools-size.vdf", "aom.dll"). So it is shown as a *sample*, not
    as a breakdown, and it is replaced the moment measured rows arrive.
    """
    mode = mode_for(entity)
    if mode == MODE_NONE:
        return Contents()

    if mode == MODE_FILES:
        paths = file_paths_of(entity)
        rows = [ContentRow(label=os.path.basename(p) or p, path=p)
                for p in paths]
        return Contents(mode=MODE_FILES, rows=rows,
                        total_bytes=int(entity.get("size_bytes", 0) or 0),
                        total_files=len(paths), provisional=True)

    names = [str(n) for n in (entity.get("children_sample") or []) if str(n)]
    rows = [ContentRow(label=n) for n in names[:8]]
    return Contents(mode=MODE_CONTENTS, rows=rows,
                    total_bytes=int(entity.get("size_bytes", 0) or 0),
                    total_files=int(entity.get("file_count", 0) or 0),
                    provisional=True)


def measure_files(paths: list, should_stop=None) -> Contents:
    """Size the files a bucket stands for, biggest first.

    Bounded by the list itself, which is short by construction: measured on a
    real all-drives scan, 67 entities carry a file list, 43 of them hold two
    files or fewer, and the longest is 25. There is no paging problem here to
    solve, so there is no paging.
    """
    rows = []
    total = 0
    for path in paths:
        if should_stop is not None and should_stop():
            break
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        total += size
        rows.append(ContentRow(label=os.path.basename(path) or path,
                               size_bytes=size, path=path, file_count=1))
    rows.sort(key=lambda r: -r.size_bytes)
    return Contents(mode=MODE_FILES, rows=rows, total_bytes=total,
                    total_files=len(rows))


# ── component rules ───────────────────────────────────────────────

def _rules() -> tuple:
    """``((relative path, display name), …)`` — most specific first.

    Loaded from classification_rules.json, beside the cache-source hints that
    already live there. Every rule is matched **relative to the entity's own
    folder**, which is why they are all more than one segment deep: a bare
    "userdata" means Steam's cloud saves under Steam and nothing in
    particular anywhere else, and a rule that fires on the wrong app is worse
    than no rule.
    """
    from app.services.entity_detector import _RULES
    raw = _RULES.get("component_rules") or {}
    items = [(str(k).replace("\\", "/").strip("/").lower(), str(v))
             for k, v in raw.items() if k and v]
    return tuple(sorted(items, key=lambda kv: -kv[0].count("/")))


def rule_for(relative: str, rules=None) -> tuple:
    """``(rule path, display name)`` covering *relative*, or ``("", "")``."""
    rel = (relative or "").replace("\\", "/").strip("/").lower()
    if not rel:
        return "", ""
    for path, name in (rules if rules is not None else _rules()):
        if rel == path or rel.startswith(path + "/"):
            return path, name
    return "", ""


# ── the measured breakdown ────────────────────────────────────────

# Long enough to finish nearly everything, short enough that a click still
# feels like a click. Steam needs ~390 ms; most entities need under 20.
DEFAULT_BUDGET_MS = 700

# Below this share of the folder a row is noise, and it joins "Other".
_MIN_ROW_SHARE = 0.01
_MAX_ROWS = 6


def walk_contents(root: str, budget_ms: int = DEFAULT_BUDGET_MS,
                  rules=None, should_stop=None) -> Contents:
    """Measure what is inside *root*, one level down, rules applied.

    Buckets by the deepest matching rule, falling back to the top-level child
    the bytes belong to. One pass, so a rule two levels down ("steamapps/
    common") costs nothing extra over the plain walk that has to happen
    anyway.

    Returns whatever was measured when the budget ran out, with
    ``truncated`` set — a short answer that admits it is short beats a
    confident one that is wrong.
    """
    if rules is None:
        rules = _rules()
    started = time.perf_counter()
    buckets: dict[str, list] = {}      # key -> [bytes, files, label, named]
    total_bytes = total_files = 0
    truncated = False

    def out_of_time() -> bool:
        if should_stop is not None and should_stop():
            return True
        return (time.perf_counter() - started) * 1000 > budget_ms

    def add(key: str, label: str, named: bool, size: int, relative: str = ""):
        # *relative* is carried so the row can be given a real path. The row
        # used to derive one from the bucket key, which is a grouping token
        # rather than a location: "child:chrome" yielded the path "chrome",
        # and "rule:cache/cache_data" yielded "cache/cache_data". Clicking such
        # a row asked the AI about a path that does not exist anywhere, and the
        # lookup's failure was reported as "This file is no longer on disk".
        row = buckets.get(key)
        if row is None:
            buckets[key] = [size, 1, label, named, relative]
        else:
            row[0] += size
            row[1] += 1

    root_norm = (root or "").replace("\\", "/").rstrip("/")
    if not root_norm or not os.path.isdir(root):
        return Contents()

    # (absolute path, path relative to root, the top-level child it is under)
    stack = [(root, "", "")]
    while stack:
        if out_of_time():
            truncated = True
            break
        current, relative, top = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            child_rel = f"{relative}/{entry.name}".strip("/")
            child_top = top or entry.name
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if is_dir:
                stack.append((entry.path, child_rel, child_top))
                continue
            try:
                size = entry.stat(follow_symlinks=False).st_size
            except OSError:
                size = 0
            total_bytes += size
            total_files += 1
            rule_path, rule_name = rule_for(child_rel, rules)
            if rule_name:
                add("rule:" + rule_path, rule_name, True, size, rule_path)
            elif child_top:
                # child_top keeps its real case; the key is lowercased only to
                # group case-variant siblings together.
                add("child:" + child_top.lower(), child_top, False, size, child_top)
            else:
                add("loose", "", False, size)

    rows = [ContentRow(label=label, size_bytes=size, file_count=files,
                       named=named,
                       path=(f"{root_norm}/{relative}" if relative else ""))
            for (size, files, label, named, relative) in buckets.values()]
    rows = _condense(rows, total_bytes)
    return Contents(mode=MODE_CONTENTS, rows=rows, total_bytes=total_bytes,
                    total_files=total_files, truncated=truncated)


def _condense(rows: list, total: int) -> list:
    """Named concepts first, then the biggest folders, then one Other row."""
    rows.sort(key=lambda r: (not r.named, -r.size_bytes))
    kept, spilled = [], []
    for row in rows:
        share = (row.size_bytes / total) if total else 0
        if len(kept) < _MAX_ROWS and (row.named or share >= _MIN_ROW_SHARE):
            kept.append(row)
        else:
            spilled.append(row)
    if spilled:
        kept.append(ContentRow(
            label="", size_bytes=sum(r.size_bytes for r in spilled),
            file_count=sum(r.file_count for r in spilled)))
    return kept


# ── the sentence that stops a bad deletion ────────────────────────

def removal_consequence(entity: dict, contents: Contents) -> str:
    """A consequence worth saying out loud, or "".

    Deliberately not part of the AI assessment: bulk AI is off by default
    (``ai_findings_enabled``), so an explanation that only exists once a model
    has run is one most people never see.

    Just as deliberately, not a restatement of the table above it. A line
    reading "Removing this deletes 29 files in 8 folders, including
    checkpoints (54.3 GB), LM (33.3 GB), loras (2.8 GB)" under a table saying
    exactly that adds nothing and trains the reader to skip the row that
    might one day matter. It speaks only when it knows something the contents
    section cannot show:

    * that the deletion leaves the folder itself in place;
    * that it will propagate to a cloud account and other devices;
    * (partial coverage is a marker on the section header, not a sentence
      here — "the scan stopped measuring" is Podbye talking about itself);
    * or, when there is no contents section at all, how much is in there.
    """
    from app.i18n import tr

    if contents.mode == MODE_FILES:
        # Not obvious from a list of file names: the folder survives.
        return tr("Only these files are removed \u2014 the folder they are in "
                  "stays.")

    provider = (entity.get("cloud_sync_provider") or "").strip()
    if provider:
        return tr("This is inside {provider}. Deleting it removes it from "
                  "your account and every synced device.", provider=provider)

    if contents.mode in (MODE_CONTENTS, MODE_ITEMS) and contents.rows:
        # The table already answers "what is inside". Nothing to add.
        return ""

    files = contents.total_files or int(entity.get("file_count", 0) or 0)
    folders = int(entity.get("folder_count", 0) or 0)
    if not files and not folders:
        return ""
    if folders:
        return tr("Removing this deletes {files:,} files in {folders:,} "
                  "folders.", files=files, folders=folders)
    return tr("Removing this deletes {files:,} files.", files=files)
