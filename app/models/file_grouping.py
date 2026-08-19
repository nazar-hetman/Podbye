"""Group a pile of file paths into the few kinds a person can decide about.

The per-file list in the inspector used to be exactly that -- a flat list, in
whatever order ``scandir`` returned it, one row per file with a full-weight
"Ask AI" button on every row. On a real entity that meant five 800-byte PNGs
rendered as five decisions, and 4 KB of Discord icons was given the same
visual budget as a 2 GB video sitting further down the same list.

Nothing here decides *for* the user. It answers the two questions a flat list
cannot: what kinds of thing are in here, and which of them are worth looking
at. A group carries its own totals so the small stuff can be acted on in one
tick, and it is still listed, still counted, still selectable -- the rule from
the entity list holds here too: **grouping never hides**.

The extension table below is deliberately its own thing, not shared with
``entity_detector``'s classification sets. Those sets decide what an entity
*is*, which drives its risk and its actions; these decide which pile a row is
drawn in. They answer to different pressures -- a display bucket may merge two
semantic types because a user reads them as one word -- so they are allowed to
drift apart rather than being wired together and constrained by each other.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field

# -- Display buckets ------------------------------------------------
# Keys are stable identifiers; the caller translates them for display.
_KIND_BY_EXT: dict[str, str] = {}


def _register(kind: str, *exts: str) -> None:
    for e in exts:
        _KIND_BY_EXT[e] = kind


_register("Images", ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif",
          ".raw", ".svg", ".webp", ".heic", ".heif", ".ico", ".cr2", ".nef",
          ".arw", ".dng", ".rw2", ".orf", ".pef", ".avif", ".jfif")
_register("Videos", ".mp4", ".mkv", ".mov", ".avi", ".wmv", ".flv", ".webm",
          ".m4v", ".ts", ".mts", ".m2ts", ".vob", ".mpg", ".mpeg")
_register("Audio", ".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a",
          ".opus", ".alac", ".aiff", ".mid", ".midi")
_register("Archives", ".zip", ".rar", ".7z", ".tar", ".gz", ".iso", ".bz2",
          ".xz", ".tgz", ".cab", ".zst", ".lz4")
_register("Installers", ".msi", ".msix", ".appx", ".appxbundle", ".dmg",
          ".deb", ".rpm")
_register("Documents", ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt",
          ".pptx", ".txt", ".md", ".csv", ".rtf", ".odt", ".ods", ".epub")
_register("Creative projects", ".psd", ".ai", ".xd", ".sketch", ".fig",
          ".afdesign", ".afphoto", ".prproj", ".aep", ".aet", ".drp", ".blend")
_register("AI models", ".gguf", ".safetensors", ".pt", ".pth", ".onnx",
          ".ckpt", ".h5", ".tflite", ".ggml")
_register("Disk images", ".vdi", ".vmdk", ".vhd", ".vhdx", ".qcow2", ".qed",
          ".vbox", ".img")
_register("Databases", ".sqlite", ".sqlite3", ".sqlite-wal", ".sqlite-shm",
          ".db", ".db-wal", ".db-shm", ".mdb", ".accdb", ".ldf", ".mdf",
          ".realm")
# .blf / .regtrans-ms are the registry's own transaction logs, .evtx / .etl
# the Windows event and trace logs -- all of them noise a user is right to
# clear out, and all of them were landing in "Other files".
_register("Logs & backups", ".log", ".bak", ".old", ".backup", ".orig",
          ".swp", ".tmp", ".temp", ".dmp", ".crash", ".evtx", ".etl", ".blf",
          ".regtrans-ms", ".trace")
_register("Programs & libraries", ".exe", ".dll", ".sys", ".so", ".dylib",
          ".pyd", ".node", ".jar", ".bin", ".pak", ".wasm", ".pdb", ".lib",
          ".msix", ".winmd")
_register("Code & config", ".py", ".js", ".ts", ".tsx", ".jsx", ".c", ".cpp",
          ".h", ".hpp", ".cs", ".java", ".go", ".rs", ".rb", ".php", ".lua",
          ".sh", ".ps1", ".bat", ".cmd", ".json", ".jsonl", ".yaml", ".yml",
          ".toml", ".ini", ".cfg", ".conf", ".config", ".env", ".properties",
          ".xml", ".html", ".css", ".scss", ".sql", ".gypi", ".gni", ".gn",
          ".ovpn", ".lock", ".iss", ".newcfg")
_register("Fonts", ".ttf", ".otf", ".woff", ".woff2", ".eot")
# A shortcut is a few hundred bytes pointing somewhere else. They pile up on
# a Desktop and read as clutter, which is exactly a thing to decide about.
_register("Shortcuts", ".lnk", ".url", ".rdp", ".appref-ms", ".desktop")
# Mapping and survey data. Nazar's secondary drive is largely drone/GIS work,
# so these are content, not noise -- see the secondary-drive note.
_register("Map & survey data", ".kml", ".kmz", ".gpx", ".waypoints", ".mission",
          ".las", ".laz", ".ply", ".e57", ".shp", ".shx", ".dbf", ".geojson",
          ".ecw", ".qmd", ".ept")
_register("Documents", ".rst", ".drawio", ".vsdx", ".one", ".xps")

OTHER_KIND = "Other files"


# What an extension can actually look like. Filenames carry dots that are not
# extensions -- "electron-v1.0.227-win32-x64" ends in ".227-win32-x64", and
# taking that as its type produced a bucket per build number. An extension is
# short and made of word characters, so anything else is simply not one.
_EXT_RE = re.compile(r"^\.[a-z0-9][a-z0-9_+-]{0,11}$")
_NUMERIC_EXT_RE = re.compile(r"^\.\d+$")


def extension_of(path: str) -> str:
    """The lower-cased extension of *path*, or '' when it has none.

    Two cases a plain rsplit gets wrong, both measured on a real scan:

    * A rotation suffix. "chrome_debug.log.1" ends in ".1", which says nothing;
      the extension a user means is the one before it.
    * A version tail. ".227-win32-x64" is not a file type, and treating it as
      one turned twelve builds of the same package into twelve buckets.
    """
    name = os.path.basename((path or "").replace("\\", "/")).lower()
    dot = name.rfind(".")
    if dot <= 0:
        return ""
    ext = name[dot:]
    if _NUMERIC_EXT_RE.match(ext):
        prev = name.rfind(".", 0, dot)
        if prev <= 0:
            return ""
        ext = name[prev:dot]
    return ext if _EXT_RE.match(ext) else ""


def kind_of(path: str) -> str:
    """The display bucket for one path. Never raises, never touches disk."""
    ext = extension_of(path)
    if not ext:
        return OTHER_KIND
    if ext in _KIND_BY_EXT:
        return _KIND_BY_EXT[ext]
    # ".log_backup1", ".logs", ".log2" -- every rolled-over log, without
    # naming each one.
    if ext.startswith(".log"):
        return "Logs & backups"
    return OTHER_KIND


@dataclass
class FileGroup:
    """One display bucket, with the totals needed to decide about it as a whole."""

    kind: str
    paths: list[str] = field(default_factory=list)
    total_bytes: int = 0
    newest_mtime: float = 0.0
    oldest_mtime: float = 0.0
    # Set only on a bucket named after an unrecognised extension, so the view
    # can render it as "PCM files" in the reader's language rather than
    # printing a raw key.
    ext: str = ""

    @property
    def count(self) -> int:
        return len(self.paths)


def stat_files(paths, limit: int = 1500,
               budget_s: float = 0.15) -> dict[str, tuple[int, float]]:
    """``{path: (size_bytes, mtime)}`` for *paths*, one stat call each.

    The list is stat'ed once here rather than per rendered row. The old view
    called ``os.path.getsize`` inside the row loop, so paging back and forth
    re-stat'ed the same files every time; grouping needs the sizes up front
    anyway to order the buckets, so one pass now serves both.

    Two guards, because they fail differently. *limit* bounds the work on a
    huge entity; *budget_s* bounds it on a slow one, which a count cannot --
    4,000 stats measured 107 ms warm and 580 ms cold on a local SSD, and this
    runs on the UI thread every time a row is clicked. A removable or network
    drive is slower again.

    Paths not reached are simply **absent** from the result rather than
    recorded as zero. The caller falls back to a live ``getsize`` for the
    handful of rows it actually draws, so a visible size is never a lie; only
    the bucket totals under-report, and only past the cap.
    """
    out: dict[str, tuple[int, float]] = {}
    started = time.monotonic()
    for i, p in enumerate(paths):
        if i >= limit:
            break
        # >=, not >: time.monotonic() is coarse on Windows, so a batch can
        # report exactly 0.0 elapsed and a strict compare never fires.
        if i and not i % 64 and time.monotonic() - started >= budget_s:
            break
        try:
            st = os.stat(p)
            out[p] = (st.st_size, st.st_mtime)
        except OSError:
            out[p] = (0, 0.0)
    return out


def group_files(paths, stats: dict[str, tuple[int, float]] | None = None
                ) -> list[FileGroup]:
    """Bucket *paths* by kind, biggest bucket first, biggest file first inside.

    Size order is the point: the list exists to answer "what can I clear out",
    and the answer is nearly always at the top of a size-ordered list. Ties
    keep the caller's original order, so same-size files -- or files that could
    not be stat'ed at all -- still read in the order they were collected.
    """
    if stats is None:
        stats = stat_files(paths)

    buckets: dict[str, FileGroup] = {}
    first_seen: dict[str, int] = {}

    def _add(key: str, path: str, idx: int, ext: str = "") -> None:
        g = buckets.get(key)
        if g is None:
            g = buckets[key] = FileGroup(kind=key, ext=ext)
            first_seen[key] = idx
        size, mtime = stats.get(path, (0, 0.0))
        g.paths.append(path)
        g.total_bytes += size
        if mtime:
            g.newest_mtime = max(g.newest_mtime, mtime)
            g.oldest_mtime = min(g.oldest_mtime, mtime) if g.oldest_mtime else mtime

    # Unknown extensions go to a bucket of their own first. On a real scan
    # "Other files" was the single largest bucket at 26-30%, which tells a
    # reader nothing; ".pcm x 18" tells them something. Ones too rare to be
    # worth a row are folded back below.
    for idx, p in enumerate(paths):
        k = kind_of(p)
        if k is OTHER_KIND:
            ext = extension_of(p)
            _add(ext or OTHER_KIND, p, idx, ext=ext)
        else:
            _add(k, p, idx)

    for key in [k for k in buckets if k.startswith(".")]:
        g = buckets[key]
        if g.count >= MIN_NAMED_EXT:
            continue
        for i, path in enumerate(g.paths):
            _add(OTHER_KIND, path, first_seen[key] + i)
        del buckets[key]
        first_seen.pop(key, None)

    for g in buckets.values():
        pos = {p: i for i, p in enumerate(g.paths)}
        g.paths.sort(key=lambda p: (-stats.get(p, (0, 0.0))[0], pos[p]))

    return sorted(
        buckets.values(),
        key=lambda g: (-g.total_bytes, -g.count, first_seen[g.kind]),
    )


# How many files of one unrecognised extension earn a row of their own. Below
# this they are folded into "Other files" -- a bucket per one-off extension is
# the same noise the grouping exists to remove.
MIN_NAMED_EXT = 3

# A bucket holding less than this share of the entity is trivia: it gets one
# collapsed row saying how much of it there is, instead of one row per file.
# 4 KB of icons beside a 2 GB video should cost the reader one line, not five.
TRIVIA_SHARE = 0.01

# A list this short fits on screen whole, so nothing is gained by folding part
# of it away: measured on a real session, most file lists are under 25 files
# and collapsing 5 of 19 rows only cost the user a click.
SMALL_LIST = 25

# Below this many files a bucket is cheap to scroll past, so it opens whatever
# its share of the bytes -- collapsing three rows saves nothing and hides the
# only thing the tab was opened to see.
ALWAYS_OPEN_COUNT = 3


def default_expanded(groups: list[FileGroup]) -> set[str]:
    """Kinds that should start open.

    A single bucket is always open: there is nothing to compare it against, and
    collapsing it would put a click between the user and the only content the
    tab has. So is every bucket of a list short enough to read whole. Beyond
    that a bucket opens when it holds a meaningful share of the bytes, or when
    it is short enough that opening it costs nothing.
    """
    if len(groups) <= 1:
        return {g.kind for g in groups}
    total_files = sum(g.count for g in groups)
    if total_files <= SMALL_LIST:
        return {g.kind for g in groups}
    total = sum(g.total_bytes for g in groups)
    open_kinds = {
        g.kind for g in groups
        if g.count <= ALWAYS_OPEN_COUNT
        or (total and g.total_bytes / total >= TRIVIA_SHARE)
    }
    # Never end up with everything shut. If the byte test excluded every bucket
    # -- an entity whose files all failed to stat, say -- open them all rather
    # than showing a tab of closed rows with no sizes to justify the closing.
    return open_kinds or {g.kind for g in groups}
