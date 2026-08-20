"""Findings Dashboard — Semantic Storage Overview

Transforms the Findings screen from a "giant raw filesystem table" into a
visual semantic storage dashboard with:

- Donut chart + category card list (overview)
- Drill-down navigation (category → entities → files)
- AI status visibility
- Viewed state tracking
- Back navigation

Architecture:
- Primary view: StorageOverviewWidget (donut + card list)
- Detail view: Existing table style (preserved)
- State machine: DASHBOARD → CATEGORY → ENTITY
"""
from __future__ import annotations

import sys
import os
import time
from typing import Callable, Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox,
    QLineEdit, QTextEdit, QFrame, QMessageBox,
    QScrollArea, QGridLayout, QAbstractItemView, QHeaderView,
    QSizePolicy, QStackedWidget, QTabWidget, QTreeWidget, QTreeWidgetItem
)
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QPainter, QPen

from app.widgets.pills import Badge
from app.models.finding import _format_size
from app.themes.theme_manager import get_category_colors, get_palette
from app.screens.cleanup_dialog import CleanupConfirmDialog
from app.screens.file_list_panel import FileListPanel
from app.models.findings_table_model import (
    FindingsTableModel, FindingsFilterProxy, COL_CHECK, COL_NAME,
)
from app.models.risk import (
    RISK_ORDER, normalize_risk, risk_fg as _risk_fg, risk_variant as _risk_variant,  # noqa: E501
)
from app.models.smart_entity import actionability_for_type
from app.models.entity_grouping import group_entities, group_label, owner_key
from app.models.reasons import translate_reason
from app.models.path_tree import PathNode, build_tree, collapse_single_child_chains
from app.widgets.tables import install_header_fit
from app.i18n import tr
from app.widgets.panels import apply_tactical_label
from app.widgets.controls import (
    ElidedLabel, TacticalCheckBox, TacticalComboBox, ask_ai_button_qss,
)


# ── Performance Constants ───────────────────────────────────────────

# Maximum number of blocks to render (prevent UI freeze with many categories)
MAX_BLOCKS = 20

# Debounce time for dashboard refresh (ms)
REFRESH_DEBOUNCE_MS = 100

# Minimum block size to show detailed info (pixels)
MIN_BLOCK_SIZE_DETAILED = 100
MIN_BLOCK_SIZE_COMPACT = 60
MIN_BLOCK_SIZE_TINY = 40

# Performance logging threshold (ms)
PERF_LOG_THRESHOLD_MS = 50


# Categories where selecting every item at once is genuinely useful — these
# hold regenerable / disposable data, so a bulk "Select all" saves real work.
# Deliberately excludes Applications (use Deep Uninstall), Duplicates (each
# copy may belong to a separate app), personal media/documents, and anything
# protected — there, picking items one by one is the safer default.
_BULK_SELECT_CATEGORIES = frozenset({
    "Cache & Temp",
    "System Logs",
    "Dev Artifacts",
    "Installers",
    "Archives",
    "Browser Data",
})


# New categories inherit a parent's theme color so they stay visually
# coherent with the existing palette without editing every theme.
_CATEGORY_COLOR_PARENT = {
    "Images": "Media",
    "Videos": "Media",
    "Audio": "Media",
    "Creative Projects": "Media",
    "Installers": "Applications",
    # Split out of the old "Databases & Saves"; both keep its slate blue.
    "Databases": "Databases & Saves",
    "Saves": "Databases & Saves",
    # Location categories — user content, so they read like the profile.
    "Downloads": "User Profile",
    "Desktop": "User Profile",
}


# Category strings reach the dashboard from several places — live findings,
# entities, restored session files — with inconsistent casing, so they are
# normalised before grouping. That normalisation used to be a plain .title(),
# which rewrites "AI / ML" to "Ai / Ml": a name that matches no colour key and
# no parent, so the category fell through to the "Other" swatch and its own
# colour was never drawn anywhere. Resolve through the registered spellings
# instead, and only fall back to .title() for a name nobody has registered.
_CANONICAL_CATEGORIES = {
    name.casefold(): name
    for name in (*get_category_colors("forest"), *_CATEGORY_COLOR_PARENT)
}


def canonical_category(name: str) -> str:
    """Return the registered spelling of *name*, else its title-case form."""
    cleaned = (name or "").strip()
    if not cleaned:
        return "Unknown"
    return _CANONICAL_CATEGORIES.get(cleaned.casefold(), cleaned.title())


def _get_category_color(category: str) -> str:
    """Return the theme-aware color for a category."""
    colors = get_category_colors()
    if category in colors:
        return colors[category]
    parent = _CATEGORY_COLOR_PARENT.get(category)
    if parent and parent in colors:
        return colors[parent]
    return colors.get("Other", "#252d2a")


def _status_color(risk: str) -> str:
    """Theme-aware accent color for a risk/status level (canonical)."""
    return _risk_fg(risk)


def _status_variant(risk: str) -> str:
    """Badge variant for a risk level (canonical)."""
    return _risk_variant(risk)


def _format_display_date(value: str) -> str:
    if not value or value == "—":
        return "—"
    return value


def _safe_duplicate_date(value) -> str:
    if isinstance(value, str) and value:
        return value
    try:
        ts = float(value or 0)
        if ts <= 0:
            return "—"
        return time.strftime("%Y-%m-%d", time.localtime(ts))
    except Exception:
        return "—"


def _duplicate_locations(entity: dict) -> list[dict]:
    locations = entity.get("duplicate_locations") or []
    normalized = []
    for idx, loc in enumerate(locations):
        if isinstance(loc, dict):
            path = loc.get("path", "")
            if not path:
                continue
            normalized.append({
                "path": path,
                "name": loc.get("name") or os.path.basename(str(path).replace("\\", "/")),
                "size": loc.get("size") or _format_size(loc.get("size_bytes", 0)),
                "size_bytes": loc.get("size_bytes", 0),
                "modified": loc.get("modified_display") or _safe_duplicate_date(loc.get("modified", "")),
                "role": loc.get("role") or ("keep candidate" if idx == 0 else "extra copy candidate"),
            })
        elif loc:
            normalized.append({
                "path": str(loc),
                "size": entity.get("size", "—"),
                "size_bytes": 0,
                "modified": "—",
                "role": "keep candidate" if idx == 0 else "extra copy candidate",
            })
    if normalized:
        return normalized

    for idx, path in enumerate(entity.get("children_sample") or []):
        if not path:
            continue
        normalized.append({
            "path": str(path),
            "name": os.path.basename(str(path).replace("\\", "/")),
            "size": entity.get("size", "—"),
            "size_bytes": 0,
            "modified": "—",
            "role": "keep candidate" if idx == 0 else "extra copy candidate",
        })
    return normalized


def _duplicate_extension(entity: dict) -> str:
    for loc in _duplicate_locations(entity):
        ext = os.path.splitext(loc.get("path", ""))[1].lower()
        if ext:
            return ext
    ext = os.path.splitext(entity.get("name", ""))[1].lower()
    return ext if ext else ""


def _duplicate_type_label(ext: str) -> str:
    if not ext:
        return "file"
    if ext in (".zip", ".rar", ".7z", ".tar", ".gz", ".iso", ".bz2", ".xz"):
        return "archive"
    if ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".tif", ".tiff", ".raw"):
        return "image"
    if ext in (".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"):
        return "video"
    if ext in (".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a"):
        return "audio"
    if ext in (".dll", ".sys", ".ocx", ".drv"):
        return "system file"
    if ext in (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt"):
        return "document"
    return f"{ext} file"


def _useful_duplicate_name(name: str) -> str:
    name = str(name or "").strip()
    if "·" in name:
        name = name.split("·", 1)[0].strip()
    if not name:
        return ""
    if name.lower() in {"duplicate files", "duplicate file group", "duplicate group", "unknown", "—"}:
        return ""
    return name


def _duplicate_representative_name(entity: dict) -> str:
    for loc in _duplicate_locations(entity):
        name = _useful_duplicate_name(loc.get("name", ""))
        if name:
            return name
        name = _useful_duplicate_name(os.path.basename(loc.get("path", "").replace("\\", "/")))
        if name:
            return name
    name = _useful_duplicate_name(entity.get("name", ""))
    if name:
        return name
    ext = _duplicate_extension(entity)
    if ext:
        label = _duplicate_type_label(ext)
        if label == "archive":
            return "Duplicate archive files"
        return f"Duplicate {ext} files"
    return "Duplicate Files"


def _duplicate_copy_count(entity: dict) -> int:
    return max(
        int(entity.get("file_count", 0) or 0),
        len(_duplicate_locations(entity)),
        1,
    )


def _duplicate_title(entity: dict) -> str:
    name = _duplicate_representative_name(entity)
    copies = _duplicate_copy_count(entity)
    if name == "Duplicate Files" or name.startswith("Duplicate "):
        return f"{name} · {copies} copies"
    return f"{name} · {copies} copies"


def _short_parent(path: str, segments: int = 2) -> str:
    """Collapse a long absolute path to its last few segments for display.

    e.g. ``E:/My Projects/irizi-odm-dev/lib/.../site-packages/cv2`` →
    ``…/site-packages/cv2``. Keeps the readable tail, drops the noisy prefix.
    """
    norm = str(path or "").replace("\\", "/").rstrip("/")
    if not norm:
        return ""
    parts = [p for p in norm.split("/") if p]
    if len(parts) <= segments:
        return norm
    return "…/" + "/".join(parts[-segments:])


def _norm_path(path: str) -> str:
    """One slash style for display. Scanned paths mix both within one string.

    Shortening is ElidedLabel's job now — it fits the width the panel actually
    has, where the character budget this replaced always cut at 54 regardless
    of how wide the window was.
    """
    return str(path or "").replace("\\", "/")


def _duplicate_short_parents(entity: dict) -> list[str]:
    """De-duplicated, shortened parent folders for a duplicate group."""
    shorts: list[str] = []
    seen: set[str] = set()
    for loc in _duplicate_locations(entity):
        parent = loc.get("parent") or os.path.dirname(loc.get("path", ""))
        sp = _short_parent(parent)
        if not sp:
            continue
        key = sp.lower()
        if key in seen:
            continue
        seen.add(key)
        shorts.append(sp)
    return shorts


def _duplicate_subtitle(entity: dict) -> str:
    copies = _duplicate_copy_count(entity)
    reclaimable = entity.get("dup_reclaimable", 0) or entity.get("reclaimable_bytes", 0)
    shorts = _duplicate_short_parents(entity)
    used_by = ""
    if shorts:
        shown = shorts[:2]
        used_by = " · Used by: " + " · ".join(shown)
        if len(shorts) > len(shown):
            used_by += f" · +{len(shorts) - len(shown)} more"
    return (
        f"{copies} copies · {_format_size(reclaimable)} reclaimable"
        f"{used_by}"
    )


def _duplicate_row_meta(entity: dict) -> str:
    """Compact one-line meta for a duplicate row.

    The title already shows ``name · N copies`` and the right column shows the
    total size, so the row meta only needs the reclaimable saving plus a short
    hint of where the copies live — not full absolute paths.
    """
    reclaimable = entity.get("dup_reclaimable", 0) or entity.get("reclaimable_bytes", 0)
    shorts = _duplicate_short_parents(entity)
    parts: list[str] = []
    if reclaimable:
        parts.append(f"{_format_size(reclaimable)} reclaimable")
    if shorts:
        shown = shorts[:2]
        where = "in " + ", ".join(shown)
        if len(shorts) > len(shown):
            where += f" +{len(shorts) - len(shown)}"
        parts.append(where)
    return " · ".join(parts) if parts else _duplicate_subtitle(entity)


def _duplicate_path_preview(entity: dict) -> str:
    """Primary copy path only.

    The full set of copies is shown once in the DUPLICATE COPIES block, so the
    PATH field no longer repeats the "also in / +N more" list.
    """
    locations = _duplicate_locations(entity)
    if locations:
        return locations[0]["path"]
    return entity.get("path", "—")


def _duplicate_recommendation(entity: dict) -> str:
    return (
        "Same content hash found in multiple locations. "
        "Keep the copy used by an installed app or project; remove only obvious extra copies."
    )


def _duplicate_locations_text(entity: dict) -> str:
    locations = _duplicate_locations(entity)
    if not locations:
        return "No duplicate locations were captured for this saved group."
    lines = []
    for idx, loc in enumerate(locations, 1):
        # Short, readable path (tail segments) — the full primary path is
        # already shown in the PATH field, so this avoids repeating long roots.
        short = _short_parent(loc.get("path", ""), 3)
        lines.append(
            f"{idx}. {short}\n"
            f"   {loc.get('size', '—')} · {loc.get('modified', '—')} · {loc.get('role', 'review')}"
        )
    return "\n".join(lines)


# File kinds where every copy is required by its owning program — deleting one
# copy breaks that app. Duplicates here are NOT spare space to reclaim.
_BINARY_DUP_EXTS = frozenset({
    ".dll", ".pyd", ".so", ".dylib", ".exe", ".sys", ".lib", ".bin",
    ".node", ".ocx", ".drv", ".a", ".framework",
})


def _duplicate_is_per_app_binary(entity: dict) -> bool:
    """True when the duplicate is a binary that each app ships its own copy of.

    Combines the file kind (library/executable) with the detector's ownership
    verdict (Review/Protected = lives inside an app, runtime, or Windows tree).
    """
    if _duplicate_extension(entity) not in _BINARY_DUP_EXTS:
        return False
    return normalize_risk(entity.get("risk", "Review")) in ("Review", "Protected")


def _duplicate_explanation(entity: dict) -> str:
    """Plain-language reasoning for a duplicate group.

    Replaces the old raw path dump with an actual explanation of what the group
    is and the safe way to act on it — including the warning that per-app
    binaries must not be deleted individually.
    """
    copies = _duplicate_copy_count(entity)
    reclaimable = entity.get("dup_reclaimable", 0) or entity.get("reclaimable_bytes", 0)
    shorts = _duplicate_short_parents(entity)
    where = ", ".join(shorts[:3]) if shorts else "several locations"
    saving = _format_size(reclaimable) if reclaimable else "space"

    if _duplicate_is_per_app_binary(entity):
        return (
            f"{copies} identical copies of this file exist, each inside a separate "
            f"application or runtime ({where}). These are not spare copies — every "
            f"program ships and depends on its own, so deleting one will likely break "
            f"that application. To recover the {saving}, uninstall a program you no "
            f"longer use (Deep Uninstall) instead of removing this file."
        )
    return (
        f"{copies} identical copies were found ({where}). The newest copy is kept as "
        f"the original; the rest are extra and can be moved to the Recycle Bin to "
        f"reclaim {saving}. Make sure none of the copies is still in active use first."
    )


def _entity_activity_text(entity: dict) -> str:
    if entity.get("entity_type") == "duplicate_group":
        locations = _duplicate_locations(entity)
        if locations and locations[0].get("modified") != "—":
            return tr("Newest copy {when}", when=locations[0]["modified"])
        return tr("Duplicate activity unknown")
    install_date = entity.get("install_date", "")
    if install_date:
        return tr("Installed {when}", when=install_date)
    accessed = entity.get("last_access", "")
    if accessed and accessed != "—":
        return tr("Last active {when}", when=accessed)
    modified = entity.get("first_seen", "")
    if modified and modified != "—":
        return tr("Updated {when}", when=modified)
    age = entity.get("age", "")
    if age and age != "—":
        return tr("Age {age}", age=age)
    return tr("Recent activity unknown")


def _entity_importance_text(entity: dict) -> str:
    if entity.get("entity_type") == "duplicate_group":
        if entity.get("risk") == "Protected":
            return tr("High — duplicate touches protected system locations")
        if entity.get("risk") == "Review":
            return tr("High — verify app/project ownership before removing copies")
        return tr("Medium — remove only clear extra copies")
    risk = entity.get("risk", "Review")
    if entity.get("cloud_sync_provider"):
        return tr("High — synced with cloud storage")
    if risk == "Protected":
        return tr("High — protected by system rules")
    if risk == "Review":
        return tr("High — may contain personal or app data")
    if risk == "Optional":
        return tr("Medium — likely removable if no longer needed")
    return tr("Low — generally safe to regenerate")


def _entity_file_group_size(entity: dict) -> int:
    """How many individual files this entity stands for, if it is a group.

    Loose buckets ("Loose archives in Downloads"), archive groups and installer
    groups carry the exact file list they were built from, and each of those
    files can be kept or recycled on its own. A folder-backed entity — an app, a
    game, a download kept whole — carries no list, because its meaning is the
    folder rather than the files inside it, however many there are.

    Reading the stored list costs nothing; the alternative signal, counting the
    folder, would mean a disk walk per visible row.
    """
    return len([p for p in (entity.get("removable_file_paths") or []) if p])


def _entity_contains_text(entity: dict) -> str:
    if entity.get("entity_type") == "duplicate_group":
        return _duplicate_subtitle(entity)
    file_count = entity.get("file_count", 0)
    folder_count = entity.get("folder_count", 0)
    parts = []
    if file_count:
        parts.append(tr("{n:,} files", n=file_count))
    if folder_count:
        parts.append(tr("{n:,} folders", n=folder_count))
    # entity_type_label comes from the ENTITY_TYPES table, whose values are
    # translated — but only if someone calls tr() on them. This is the row
    # subtitle under every finding, so it was the most-repeated piece of
    # English in a translated build.
    label = entity.get("entity_type_label") or entity.get("semantic_label") or ""
    if label:
        parts.append(tr(label))
    # Say so when the row stands for a list rather than a folder. Without this
    # "Loose archives in Downloads" looks like one indivisible thing, and the
    # per-file view — which is what a group row is for — goes unnoticed.
    if _entity_file_group_size(entity) >= 2:
        parts.append(tr("choose individual files"))
    return " · ".join(parts) if parts else tr("Contents not summarized")


def _entity_context_text(entity: dict) -> str:
    entity_type = entity.get("entity_type", "")
    if entity_type == "duplicate_group":
        return "Same content hash found in multiple locations"
    mapping = {
        "application": "Installed application files",
        "portable_app": "Portable application files",
        "installer": "Installer package",
        "installer_group": "Installer collection",
        "archive_group": "Archive collection",
        "backup_group": "Backup set",
        "duplicate_group": "Duplicate file match",
        "browser_profile": "Browser profile data",
        "database": "App or user database",
        "dev_project": "Development project",
        "dev_artifacts": "Generated development files",
        "cache_folder": "Temporary or cache data",
        "temp_folder": "Temporary files",
        "shader_cache": "Graphics cache",
        "log_folder": "Diagnostic log files",
        "protected_system": "Protected system location",
    }
    if entity_type in mapping:
        return mapping[entity_type]

    source_rule = entity.get("source_rule", "")
    if source_rule.startswith("entity detection: "):
        source_rule = source_rule.replace("entity detection: ", "")
    if source_rule and source_rule != "—":
        return source_rule.replace("_", " ").capitalize()
    return entity.get("category", "Unknown")


_APP_SMART_ACTION_TYPES = frozenset({
    "application",
    "portable_app",
    "installer",
    "installer_group",
})


def _entity_actionability(entity: dict) -> str:
    """recycle | uninstall | review_only | protected for a finding dict.

    Reads the value baked in by SmartEntity.to_dict, falling back to deriving
    it from the entity type so findings restored from older saved sessions
    (which predate the field) are still gated correctly.
    """
    return entity.get("actionability") or actionability_for_type(
        entity.get("entity_type", ""), normalize_risk(entity.get("risk", "Review"))
    )


def _finding_for_path(path: str):
    """Build a Finding for *path* from disk, or None if it is not there.

    "Ask AI" on a file used to look the path up in the scan's findings list and
    give up when it was missing — which is *every* file on a reopened session,
    because a large scan deliberately does not persist its 1.8M raw findings.
    Measured on a real 1,258-entity session: 79 of 79 buckets answered "This
    file is not part of the current scan results". The Files tab also lists
    files from a live folder listing, which were never findings in the first
    place.

    Nothing here needs the scan: the file is on disk, and its path, size and
    dates are the whole input to the prompt. A live object from the model is
    still preferred when there is one, so the answer lands on the instance the
    rest of the UI reads from.
    """
    if not path:
        return None
    try:
        st = os.stat(path)
    except OSError:
        return None
    from app.models.finding import Finding, categorize
    name = os.path.basename(path) or path
    ext = os.path.splitext(name)[1].lower()
    is_dir = os.path.isdir(path)
    category, source_rule, semantic_label, confidence = categorize(
        path, name, ext, is_dir, st.st_size)
    return Finding(
        path=path, name=name, is_dir=is_dir, size_bytes=st.st_size,
        extension=ext, modified=st.st_mtime, accessed=st.st_atime,
        parent=os.path.dirname(path), category=category,
        source_rule=source_rule, semantic_label=semantic_label,
        owner_confidence=confidence,
    )


def _is_content_container(entity: dict) -> bool:
    """True for personal / mixed / ambiguous folders that must not be bulk-deleted."""
    return _entity_actionability(entity) == "review_only"


def _container_explanation(entity: dict) -> str:
    """Plain-language help for a content container — what it is and how to act.

    Replaces a bare "go verify this yourself" with a useful read of the folder:
    its size/contents and the safe way to reclaim space without nuking personal
    data.
    """
    name = entity.get("name") or "This folder"
    size = entity.get("size", "—")
    file_count = int(entity.get("file_count", 0) or 0)
    folder_count = int(entity.get("folder_count", 0) or 0)
    entity_type = entity.get("entity_type", "")
    category = entity.get("category", "")
    where = f"{file_count:,} files" + (f" across {folder_count:,} folders" if folder_count else "")
    scale = f"{where} ({size})" if where else size

    if entity_type in ("mixed_folder", "unknown_folder", "loose_files"):
        return (
            f"{name} holds {scale} of mixed or unrecognized content. Because the "
            f"files aren't all one kind, deleting the whole folder could remove "
            f"things you still want. Open it to see what's inside, or clean only "
            f"specific items — duplicates and very old large files are the safe wins."
        )
    if entity_type == "dev_project":
        return (
            f"{name} looks like a source/project folder ({scale}). The source is "
            f"yours to keep — instead of deleting it, reclaim space from generated "
            f"parts (build output, node_modules, caches) shown separately under Dev Artifacts."
        )
    label = (category or "personal").lower()
    return (
        f"{name} is a personal {label} location with {scale}. Vigil keeps personal "
        f"data intact, so it won't bulk-delete this folder. Open it to review, or "
        f"target only reclaimable items inside — duplicates or files untouched for years."
    )


def _is_application_action_target(entity: dict) -> bool:
    """True when a finding should expose system-level app actions."""
    entity_type = str(entity.get("entity_type", "")).lower()
    if entity_type in _APP_SMART_ACTION_TYPES:
        return True
    if str(entity.get("category", "")).lower() == "applications":
        return True
    if any(entity.get(k) for k in ("app_version", "app_publisher", "install_date")):
        return True

    label_text = " ".join(
        str(entity.get(k, ""))
        for k in ("entity_type_label", "semantic_label", "source_rule", "why")
    ).lower()
    return any(term in label_text for term in (
        "installed application",
        "portable application",
        "application binary",
        "software package",
        "installer package",
        "installer collection",
        "package manager",
    ))


# The inspector's field labels. They reach tr() through _mk_key(), so the
# static scan over tr("literal") calls cannot see them — listed here so the
# translation-coverage test can. Every one of them stayed English in
# translated builds until they were wrapped.
INSPECTOR_FIELD_LABELS = (
    "CATEGORY:", "TYPE:", "PATH:", "SIZE:",
    "CONTAINS:", "LAST ACTIVE:", "IMPORTANCE:",
)


def _has_uninstaller(entity: dict) -> bool:
    """True when the app exposes an uninstaller that can actually be run.

    Not merely "the registry mentions one": 19 of 475 uninstall commands on a
    real machine pointed at an executable that no longer existed, so the
    button promised something Vigil could never deliver.
    """
    from app.services.uninstaller import uninstaller_is_runnable
    return uninstaller_is_runnable(entity.get("uninstall_string") or "")


def _finding_rgba(hex_color: str, alpha: int) -> str:
    color = QColor(hex_color)
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {alpha})"


def _finding_recommendation(entity: dict) -> tuple[str, str, str, str]:
    """Return (status, recommendation, evidence, accent_color)."""
    p = get_palette()
    risk = normalize_risk(entity.get("risk", "Review"))
    is_duplicate = entity.get("entity_type") == "duplicate_group"
    is_app = _is_application_action_target(entity)
    category = entity.get("category", "Unknown")
    size = entity.get("size", "—")
    accent_safe = p.get("safe", "#7cc596")
    accent_review = p.get("review", "#d8b46a")
    accent_risk = p.get("risk", "#d68a78")
    accent_info = p.get("accent", "#7ab8d4")

    if entity.get("is_self"):
        return (
            tr("THAT'S ME"),
            # No trailing smiley: U+1F642 has no glyph in the bundled fonts or
            # in Segoe UI Symbol, so it drew as a .notdef box mid-sentence.
            tr("Recommendation: this is Vigil — the app doing the cleaning. You can "
               "remove it whenever you like, but it would be good to let it finish "
               "the job first."),
            tr("Vigil's own files: the app, your settings, and the scan history "
               "this screen is showing. Vigil will not clean itself up."),
            accent_info,
        )
    if risk == "Protected":
        return (
            tr("PROTECTED"),
            tr("Recommendation: keep this item. It is marked protected and should not be cleaned from Findings."),
            translate_reason(entity) or tr("Protected rule matched this path or entity type."),
            accent_risk,
        )
    if is_duplicate and _duplicate_is_per_app_binary(entity):
        return (
            tr("KEEP COPIES"),
            tr("Recommendation: keep every copy — each belongs to a separate program that needs its own. To free space, uninstall an app you no longer use instead of deleting this file."),
            translate_reason(entity) or _duplicate_subtitle(entity),
            accent_review,
        )
    if not is_duplicate and not is_app and _is_content_container(entity):
        return (
            tr("REVIEW INSIDE"),
            tr("Recommendation: Vigil won't delete this whole folder — it holds personal or mixed content. Open it to review, or reclaim space from specific items inside (duplicates, very old large files)."),
            translate_reason(entity) or tr("Personal or mixed content — deleting everything here is rarely what you want."),
            accent_review,
        )
    if is_app:
        return (
            tr("SYSTEM-LEVEL"),
            tr("Recommendation: use Deep Uninstall for applications; recycle only leftover files you recognize."),
            translate_reason(entity) or tr("Application metadata or installer/package signals were detected."),
            accent_review,
        )
    if is_duplicate:
        return (
            tr("DUPLICATE"),
            tr("Recommendation: remove only obvious extra copies and keep the copy used by an app or active project."),
            _duplicate_subtitle(entity),
            accent_info,
        )
    if risk == "Safe":
        return (
            tr("LOW CONCERN"),
            tr("Recommendation: safe to move to the Recycle Bin if you do not need this cache or generated data."),
            tr("{category} · {size} · usually recoverable from the Recycle Bin.").format(category=category, size=size),
            accent_safe,
        )
    if risk == "Optional":
        return (
            tr("OPTIONAL"),
            tr("Recommendation: clean this only if the path and contents are familiar."),
            entity.get("recommendation") or translate_reason(entity) or tr("This is likely removable but may still be useful."),
            accent_info,
        )
    return (
        tr("NEEDS REVIEW"),
        tr("Recommendation: inspect the path, owner, and AI reasoning before cleanup."),
        translate_reason(entity) or tr("Vigil does not have enough confidence to mark this as safe."),
        accent_review,
    )

# Sort options for detail view
# The dropdown is built from the proxy's own key list, not a second copy of
# it. The copy that used to live here had drifted: it advertised "Safe
# cleanup" and "Last accessed", which lessThan() had no branch for, so both
# silently sorted by size and were indistinguishable from "Largest first".
SORT_OPTIONS = FindingsFilterProxy.SORT_KEYS


def get_contrast_color(bg_color_hex: str) -> str:
    """Return black or white text color based on background brightness."""
    try:
        h = bg_color_hex.lstrip('#')
        if len(h) < 6:
            return "#ffffff"
        r = int(h[0:2], 16) / 255.0
        g = int(h[2:4], 16) / 255.0
        b = int(h[4:6], 16) / 255.0
        luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
        return "#ffffff" if luminance < 0.5 else "#1a1a1a"
    except (ValueError, IndexError):
        return "#ffffff"


# ── Donut chart custom widget ─────────────────────────────────────

class DonutChartWidget(QWidget):
    """Custom QPainter donut chart — proportional sectors per category.

    Emits sector_clicked(category: str) when a sector is clicked or hovered.
    """

    sector_clicked = Signal(str)
    sector_hovered = Signal(str)   # emits category name or "" on leave

    def __init__(self, parent=None):
        super().__init__(parent)
        self._segments: list[dict] = []   # {cat, pct, color, angle_start, angle_span}
        self._category_count: int = 0     # all categories, not just the wedges
        self._total_bytes: int = 0
        self._scan_label: str = ""
        self._selected: str = ""
        self._hovered: str = ""
        self.setMinimumSize(220, 220)
        self.setMaximumSize(300, 300)
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)

    # Above this, the remaining categories are pooled into one "Other" wedge.
    # A real scan finds 19, and the tail of that list is a fringe of sub-degree
    # slivers: unreadable, unclickable, and indistinguishable from each other.
    # The category list beside the chart still shows every one of them — the
    # donut answers "what is taking the space", not "what exists".
    MAX_SLICES = 8

    def set_data(self, sorted_cats: list[tuple], total_bytes: int, scan_label: str = ""):
        """sorted_cats: list of (category, data_dict) sorted by size desc."""
        self._total_bytes = total_bytes
        self._scan_label = scan_label
        self._segments.clear()

        cats = list(sorted_cats)
        # The real figure, not the number of wedges — pooling must not make the
        # centre under-report how many categories the scan actually found.
        self._category_count = len(cats)
        head, tail = cats[:self.MAX_SLICES], cats[self.MAX_SLICES:]

        total = sum(d["size_bytes"] for _, d in cats) or 1
        angle = 0.0

        def _add(cat, size_bytes, pct, color_key, is_other=False, pooled=0):
            nonlocal angle
            span = 360.0 * size_bytes / total
            self._segments.append({
                "cat": cat,
                "pct": pct,
                "size_bytes": size_bytes,
                # The category to resolve a colour FROM, which is not always
                # what the wedge is labelled: the pooled wedge is labelled in
                # the user's language and would never match a palette key.
                # Resolved at paint time, never cached — a cached hex survives
                # a theme switch and leaves the chart wearing the old palette.
                "color_key": color_key,
                "angle_start": angle,
                "angle_span": span,
                "is_other": is_other,
                "pooled": pooled,
            })
            angle += span

        for cat, data in head:
            _add(cat, data["size_bytes"], data.get("percentage", 0), cat)

        if tail:
            pooled_bytes = sum(d["size_bytes"] for _, d in tail)
            _add(tr("Other"), pooled_bytes,
                 sum(d.get("percentage", 0) for _, d in tail),
                 "Other", is_other=True, pooled=len(tail))
        self.update()

    # Angular separator carved between neighbouring wedges.
    GAP_DEG = 1.4
    # No wedge is drawn thinner than this, so a category with a real share
    # never vanishes entirely into its own separator.
    MIN_SPAN_DEG = 0.25

    @classmethod
    def _drawn_span(cls, angle_span: float) -> float:
        """Sweep actually painted for a wedge of *angle_span* degrees.

        The gap is carved out of the wedge, so it can never exceed it. A slice
        narrower than the gap used to yield a negative sweep, and Qt draws a
        negative sweep counter-clockwise — the sliver was painted backwards
        across its neighbours, which is the speckled band the ring showed.
        """
        gap = min(cls.GAP_DEG, angle_span * 0.4)
        return max(angle_span - gap, cls.MIN_SPAN_DEG)

    def set_selected(self, category: str):
        self._selected = category
        self.update()

    def paintEvent(self, event):
        from PySide6.QtCore import QRectF
        import math

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        W, H = self.width(), self.height()
        margin = 14
        diameter = min(W, H) - margin * 2
        cx = W // 2
        cy = H // 2
        rect = QRectF(cx - diameter / 2, cy - diameter / 2, diameter, diameter)

        # Thin ring — large hole fraction keeps the chart supportive,
        # not a dominant solid disc.
        HOLE_FRAC = 0.70
        half = HOLE_FRAC / 2.0
        hole_rect = QRectF(
            cx - diameter * half,
            cy - diameter * half,
            diameter * HOLE_FRAC,
            diameter * HOLE_FRAC,
        )

        for seg in self._segments:
            a_start = seg["angle_start"]
            a_span  = seg["angle_span"]

            is_sel = seg["cat"] == self._selected
            is_hov = seg["cat"] == self._hovered

            color = QColor(_get_category_color(seg["color_key"]))
            if is_sel:
                color = color.lighter(138)
            elif is_hov:
                color = color.lighter(118)

            painter.setBrush(color)
            # Hairline separator between sectors, drawn in the panel bg.
            painter.setPen(QPen(QColor(get_palette().get("bg_deep", "#0a100c")), 1))

            # Qt: angles are 1/16th degree, start=top (90°), CCW positive.
            # We map: 0° = top, CW, so Qt angle = (90 - a_start) * 16.
            qt_start = int((90.0 - a_start) * 16)
            qt_span = int(-self._drawn_span(a_span) * 16)

            # Explode selected/hovered sector outward — restrained nudge
            draw_rect = rect
            if is_sel or is_hov:
                mid_angle_rad = math.radians(a_start + a_span / 2 - 90)
                offset = 4 if is_sel else 2
                dx = offset * math.cos(mid_angle_rad)
                dy = offset * math.sin(mid_angle_rad)
                draw_rect = QRectF(rect.x() + dx, rect.y() + dy, rect.width(), rect.height())

            painter.drawPie(draw_rect, qt_start, qt_span)

        # ── Donut hole ──────────────────────────────────────────────
        # panel_alt, not bg_deep: the donut sits inside a PanelAlt, and
        # bg_deep is far darker than that on every dark theme (#070c09 against
        # #19231c on forest), so the hole read as a black puck stuck in the
        # middle of the chart. On the light theme the two happen to be close,
        # which is why it looked right there and wrong everywhere else.
        hole_color = QColor(get_palette().get("panel_alt", "#19231c"))
        painter.setBrush(hole_color)
        painter.setPen(QPen(hole_color, 1))
        painter.drawEllipse(hole_rect)

        # ── Center text ─────────────────────────────────────────────
        # Priority: hovering > selected > default
        active = self._hovered or self._selected
        if active and self._segments:
            seg = next((s for s in self._segments if s["cat"] == active), None)
            if seg:
                label = (tr("{n} smaller categories").format(n=seg["pooled"])
                         if seg.get("is_other") else seg["cat"])
                self._draw_center_text(painter, cx, cy,
                    f"{seg['pct']:.1f}%",
                    label,
                    _format_size(seg["size_bytes"]),
                )
                return

        # Default: total size + category count (no percentage)
        self._draw_center_text(painter, cx, cy,
            _format_size(self._total_bytes),
            tr("{n} categories").format(n=self._category_count),
            self._scan_label,
        )

    def _draw_center_text(self, painter, cx, cy, line1: str, line2: str, line3: str):
        from PySide6.QtCore import QRectF, Qt as _Qt

        def _txt(text, font_family, size_px, bold, color_hex, y_offset):
            f = QFont(font_family, size_px)
            f.setBold(bold)
            painter.setFont(f)
            painter.setPen(QColor(color_hex))
            painter.drawText(
                QRectF(cx - 80, cy + y_offset - size_px, 160, size_px * 2),
                _Qt.AlignCenter | _Qt.AlignVCenter,
                text,
            )

        p = get_palette()
        _txt(line1, "JetBrains Mono", 15, True,  p.get("text",       "#c8d5c9"), -13)
        _txt(line2, "Silkscreen",      7, False, p.get("text_dim",   "#8a9b8f"),   5)
        _txt(line3, "JetBrains Mono",  8, False, p.get("text_faint", "#57685e"),  18)

    def _seg_at(self, pos) -> str:
        """Return category name for the sector under pos, or ''."""
        import math
        cx, cy = self.width() / 2, self.height() / 2
        dx, dy = pos.x() - cx, pos.y() - cy
        dist = math.sqrt(dx * dx + dy * dy)

        margin = 14
        diameter = min(self.width(), self.height()) - margin * 2
        outer_r = diameter / 2
        inner_r = outer_r * 0.70

        if dist < inner_r or dist > outer_r:
            return ""

        # Angle from top, clockwise (matches our angle_start convention)
        angle = math.degrees(math.atan2(dx, -dy)) % 360

        for seg in self._segments:
            a0 = seg["angle_start"]
            a1 = a0 + seg["angle_span"]
            if a0 <= angle < a1:
                return seg["cat"]
        return ""

    def mouseMoveEvent(self, event):
        cat = self._seg_at(event.position())
        if cat != self._hovered:
            self._hovered = cat
            self.update()
            self.sector_hovered.emit(cat)

    def leaveEvent(self, event):
        if self._hovered:
            self._hovered = ""
            self.update()
            self.sector_hovered.emit("")

    def set_hovered(self, category: str):
        """Called externally (e.g. from card hover) to highlight a segment."""
        if category != self._hovered:
            self._hovered = category
            self.update()

    def mousePressEvent(self, event):
        cat = self._seg_at(event.position())
        if not cat:
            return
        self._selected = cat
        self.update()
        # The pooled wedge stands for several categories, so there is no single
        # view to open. It still highlights and reports itself in the centre.
        if not self._is_other(cat):
            self.sector_clicked.emit(cat)

    def _is_other(self, category: str) -> bool:
        return any(s["cat"] == category and s.get("is_other") for s in self._segments)


# ── Category card for the overview list ──────────────────────────

class CategoryCardWidget(QFrame):
    """Single row in the overview category list.

    The column geometry is public because StorageOverviewWidget builds the
    header row from it — the two drifted apart once and the headers ended up
    labelling nothing.
    """

    clicked  = Signal(str)
    hovered  = Signal(str)   # emits category name or "" on leave

    MARGIN_L = 10
    MARGIN_R = 14
    SPACING  = 8
    SWATCH_W = 4
    NAME_W   = 142
    COUNT_W  = 80
    SIZE_W   = 72
    PCT_W    = 52

    def __init__(self, category: str, data: dict, parent=None):
        super().__init__(parent)
        self._category = category
        self._in_style_change = False  # re-entrancy guard
        self._is_selected = False
        self._is_hovered  = False
        self.setObjectName("CategoryCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(58)
        self._color = _get_category_color(category)
        self._build(category, data)
        self._apply_style()

    def _build(self, category: str, data: dict):
        row = QHBoxLayout(self)
        row.setContentsMargins(self.MARGIN_L, 0, self.MARGIN_R, 0)
        row.setSpacing(self.SPACING)

        # Color swatch
        swatch = QFrame()
        swatch.setFixedSize(self.SWATCH_W, 34)
        swatch.setStyleSheet(f"background: {self._color}; border: none; border-radius: 2px;")
        row.addWidget(swatch)

        # Category name
        name_wrap = QWidget()
        name_wrap.setStyleSheet("background: transparent; border: none;")
        name_row = QHBoxLayout(name_wrap)
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(0)
        self._name_lbl = QLabel(tr(category).upper())
        self._name_lbl.setFixedWidth(self.NAME_W)
        name_row.addWidget(self._name_lbl)
        row.addWidget(name_wrap)

        row.addStretch()

        p = get_palette()

        # Item count — bare number, because the column header says ITEMS. The
        # unit used to be repeated in every cell, and untranslated with it.
        self._count_lbl = QLabel(f"{data.get('count', 0):,}")
        self._count_lbl.setFixedWidth(self.COUNT_W)
        self._count_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self._count_lbl)

        # Size
        self._size_lbl = QLabel(_format_size(data.get("size_bytes", 0)))
        self._size_lbl.setFixedWidth(self.SIZE_W)
        self._size_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self._size_lbl)

        # Percentage label
        pct = data.get("percentage", 0)
        self._pct_lbl = QLabel(f"{pct:.1f}%")
        self._pct_lbl.setFixedWidth(self.PCT_W)
        self._pct_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self._pct_lbl)

        self._apply_label_colors(p)

    def _apply_label_colors(self, p: dict = None):
        if p is None:
            p = get_palette()
        faint = p.get("text_faint", "#57685e")
        dim   = p.get("text_dim",   "#8a9b8f")
        text  = p.get("text",       "#d6e2da")
        self._name_lbl.setStyleSheet(
            f"font-family: 'Silkscreen', 'JetBrains Mono'; font-size: 9px; letter-spacing: 1px; color: {text};"
        )
        self._count_lbl.setStyleSheet(f"font-family: 'JetBrains Mono'; font-size: 9px; color: {faint};")
        self._size_lbl.setStyleSheet(f"font-family: 'JetBrains Mono'; font-size: 10px; color: {dim};")
        self._pct_lbl.setStyleSheet(f"font-family: 'JetBrains Mono'; font-size: 11px; font-weight: bold; color: {text};")

    def set_selected(self, selected: bool):
        self._is_selected = selected
        self._apply_style()

    def set_hovered(self, hovered: bool):
        """Called externally (from donut hover) to mirror highlight."""
        if hovered != self._is_hovered:
            self._is_hovered = hovered
            self._apply_style()

    def _apply_style(self):
        p = get_palette()
        border = p.get("border", "#213028")
        hover = p.get("panel_hover", "#1d2c25")
        panel = p.get("panel", "#141d18")
        if self._is_selected:
            self.setStyleSheet(
                f"QFrame#CategoryCard {{ background: {self._color}18; "
                f"border: 1px solid {self._color}88; border-radius: 2px; }}"
            )
        elif self._is_hovered:
            self.setStyleSheet(
                f"QFrame#CategoryCard {{ background: {hover}; "
                f"border: 1px solid {p.get('border_hover', '#3a5648')}; border-radius: 2px; }}"
            )
        else:
            self.setStyleSheet(
                f"QFrame#CategoryCard {{ background: {panel}; "
                f"border: 1px solid {border}; border-radius: 2px; }}"
            )

    def enterEvent(self, event):
        self._is_hovered = True
        self._apply_style()
        self.hovered.emit(self._category)

    def leaveEvent(self, event):
        self._is_hovered = False
        self._apply_style()
        self.hovered.emit("")

    def changeEvent(self, event):
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.StyleChange and not self._in_style_change:
            self._in_style_change = True
            try:
                self._color = _get_category_color(self._category)
                self._apply_style()
                self._apply_label_colors()
            finally:
                self._in_style_change = False
        super().changeEvent(event)

    def mousePressEvent(self, event):
        self.clicked.emit(self._category)


# ── Storage Overview Widget (donut + card list) ───────────────────

class StorageOverviewWidget(QFrame):
    """Donut chart + synchronized category card list.

    Left: DonutChartWidget — proportional sectors
    Right: Scrollable list of CategoryCardWidgets

    Clicking either a sector or a card:
      1. highlights both the sector and the card
      2. calls on_category_click(category)
    """

    browse_by_folder = Signal()

    def __init__(self, parent=None, on_category_click: Callable = None):
        super().__init__(parent)
        self.on_category_click = on_category_click
        self._cards: dict[str, CategoryCardWidget] = {}
        self._selected: str = ""
        self._sorted_cats: list = []
        self._total_bytes: int = 0
        self._in_style_change = False
        self.setObjectName("StorageOverviewRoot")
        self.setFrameShape(QFrame.NoFrame)
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)

        # ── Header ──────────────────────────────────────────────────
        header = QHBoxLayout()
        title = QLabel(tr("STORAGE OVERVIEW"))
        apply_tactical_label(title, font_size=12, letter_spacing=3)
        header.addWidget(title)
        self._total_lbl = QLabel(tr("// analyzing..."))
        self._total_lbl.setObjectName("Muted")
        self._total_lbl.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 10px;")
        header.addWidget(self._total_lbl)
        header.addStretch()

        # The way out when a category is wrong: browse by location instead.
        self._btn_by_folder = QPushButton(tr("Browse by folder"))
        self._btn_by_folder.setObjectName("Subtle")
        self._btn_by_folder.setStyleSheet("font-size: 11px; padding: 6px 12px;")
        self._btn_by_folder.setCursor(Qt.PointingHandCursor)
        self._btn_by_folder.setToolTip(
            tr("See everything by where it lives on disk, not by what Vigil "
               "thinks it is."))
        self._btn_by_folder.clicked.connect(
            lambda: self.browse_by_folder.emit())
        header.addWidget(self._btn_by_folder)
        outer.addLayout(header)

        # ── Body: category list LEFT (primary), summary RIGHT ────────
        body = QHBoxLayout()
        body.setSpacing(14)
        body.setAlignment(Qt.AlignTop)

        # LEFT — category list panel (primary operational surface)
        self._list_panel = QFrame()
        self._list_panel.setObjectName("PanelAlt")
        self._list_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        list_outer = QVBoxLayout(self._list_panel)
        list_outer.setContentsMargins(0, 0, 0, 0)
        list_outer.setSpacing(0)

        # The header must mirror CategoryCardWidget._build exactly, or it
        # labels nothing: the cards push their figures right with a stretch
        # after the name, while this row used to pack every header hard left
        # and end with the stretch — so CATEGORY/ITEMS/SIZE/% sat bunched on
        # the left edge with the columns they name a thousand pixels away.
        list_hdr = QHBoxLayout()
        list_hdr.setContentsMargins(CategoryCardWidget.MARGIN_L, 10,
                                    CategoryCardWidget.MARGIN_R, 8)
        list_hdr.setSpacing(CategoryCardWidget.SPACING)
        # swatch column + the gap that follows it
        list_hdr.addSpacing(CategoryCardWidget.SWATCH_W)
        self._hdr_labels = []
        columns = [
            (tr("CATEGORY"), CategoryCardWidget.NAME_W, Qt.AlignLeft),
            (tr("ITEMS"),    CategoryCardWidget.COUNT_W, Qt.AlignRight),
            (tr("SIZE"),     CategoryCardWidget.SIZE_W,  Qt.AlignRight),
            ("%",            CategoryCardWidget.PCT_W,   Qt.AlignRight),
        ]
        for idx, (txt, w, align) in enumerate(columns):
            h = QLabel(txt)
            h.setFixedWidth(w)
            h.setAlignment(align | Qt.AlignVCenter)
            list_hdr.addWidget(h)
            self._hdr_labels.append(h)
            if idx == 0:
                list_hdr.addStretch()   # same place the cards put theirs
        list_outer.addLayout(list_hdr)

        self._list_sep = QFrame()
        self._list_sep.setFrameShape(QFrame.HLine)
        list_outer.addWidget(self._list_sep)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setFrameShape(QFrame.NoFrame)

        self._cards_container = QWidget()
        self._cards_layout = QVBoxLayout(self._cards_container)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(0)
        self._cards_layout.addStretch()

        scroll.setWidget(self._cards_container)
        list_outer.addWidget(scroll, 1)
        body.addWidget(self._list_panel, stretch=1)

        # RIGHT — storage summary: donut as a supporting visual + metrics
        self._summary_panel = QFrame()
        self._summary_panel.setObjectName("PanelAlt")
        self._summary_panel.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self._summary_panel.setMinimumWidth(300)
        self._summary_panel.setMaximumWidth(340)
        sp = QVBoxLayout(self._summary_panel)
        sp.setContentsMargins(16, 14, 16, 16)
        sp.setSpacing(10)

        self._summary_hdr = QLabel(tr("SUMMARY"))
        sp.addWidget(self._summary_hdr)

        self._metric_keys: list = []
        self._metric_rows: dict = {}
        for key, label in [
            ("total", tr("TOTAL SIZE")),
            ("top",   tr("TOP CATEGORY")),
            ("count", tr("CATEGORIES")),
        ]:
            row = QHBoxLayout()
            row.setSpacing(8)
            # The summary panel is capped at 340px, and the value is the
            # payload — TOP CATEGORY carries a name plus a size. So the
            # eyebrow yields (elides) and the value keeps its natural width;
            # sharing the squeeze cut "Applications · 12.0 GB" in French.
            k = ElidedLabel(label)
            row.addWidget(k, stretch=1)
            v = QLabel("—")
            v.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 12px;")
            v.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            v.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)
            row.addWidget(v)
            sp.addLayout(row)
            self._metric_keys.append(k)
            self._metric_rows[key] = v

        self._summary_sep = QFrame()
        self._summary_sep.setFrameShape(QFrame.HLine)
        sp.addWidget(self._summary_sep)

        self._donut = DonutChartWidget()
        self._donut.setFixedSize(248, 248)
        self._donut.sector_clicked.connect(self._on_sector_click)
        self._donut.sector_hovered.connect(self._on_sector_hover)
        sp.addWidget(self._donut, alignment=Qt.AlignCenter)

        body.addWidget(self._summary_panel, 0, Qt.AlignTop)

        outer.addLayout(body, stretch=1)
        self._apply_panel_colors()

    def _apply_panel_colors(self):
        p = get_palette()
        bg    = p.get("panel_alt", "#18241e")
        brd   = p.get("border",  "#213028")
        faint = p.get("text_faint", "#57685e")
        panel_qss = f"QFrame#PanelAlt {{ background: {bg}; border: 1px solid {brd}; }}"
        self._summary_panel.setStyleSheet(panel_qss)
        self._list_panel.setStyleSheet(panel_qss)
        hdr_style = f"font-family: 'Silkscreen', 'JetBrains Mono'; font-size: 7px; letter-spacing: 1px; color: {p.get('text_dim', '#8a9b8f')};"
        for lbl in self._hdr_labels:
            lbl.setStyleSheet(hdr_style)
        self._list_sep.setStyleSheet(f"background: {brd}; max-height: 1px; border: none;")
        self._summary_sep.setStyleSheet(f"background: {brd}; max-height: 1px; border: none;")
        self._summary_hdr.setStyleSheet(
            f"font-family: 'Silkscreen', 'JetBrains Mono'; font-size: 8px; letter-spacing: 2px; color: {faint};"
        )
        for idx, k in enumerate(self._metric_keys):
            k.setStyleSheet(
                f"font-family: 'Silkscreen', 'JetBrains Mono'; font-size: {9 if idx == 0 else 8}px; "
                f"letter-spacing: {2 if idx == 0 else 1}px; color: {faint};"
            )
        if "total" in self._metric_rows:
            self._metric_rows["total"].setStyleSheet(
                f"font-family: 'JetBrains Mono'; font-size: 28px; font-weight: bold; color: {p.get('text', '#d6e2da')};"
            )
        if "top" in self._metric_rows:
            self._metric_rows["top"].setStyleSheet(
                f"font-family: 'JetBrains Mono'; font-size: 13px; color: {p.get('text_dim', '#8a9b8f')};"
            )
        if "count" in self._metric_rows:
            self._metric_rows["count"].setStyleSheet(
                f"font-family: 'JetBrains Mono'; font-size: 13px; color: {p.get('text_dim', '#8a9b8f')};"
            )

    def changeEvent(self, event):
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.StyleChange:
            self._apply_panel_colors()
        super().changeEvent(event)

    # ── public API ────────────────────────────────────────────────

    def update_categories(self, category_data: dict):
        """Rebuild from fresh category data dict."""
        total_bytes = sum(d["size_bytes"] for d in category_data.values() if d["size_bytes"] > 0)
        self._total_bytes = total_bytes
        self._total_lbl.setText(tr("// {size} total", size=_format_size(total_bytes)))

        if total_bytes == 0:
            return

        sorted_cats = []
        for category, data in category_data.items():
            if data["size_bytes"] > 0:
                data["percentage"] = 100.0 * data["size_bytes"] / total_bytes
                sorted_cats.append((category, data))
        sorted_cats.sort(key=lambda x: x[1]["size_bytes"], reverse=True)
        self._sorted_cats = sorted_cats

        # Cap to MAX_BLOCKS same as map view
        if len(sorted_cats) > MAX_BLOCKS:
            other_size  = sum(d["size_bytes"]        for _, d in sorted_cats[MAX_BLOCKS - 1:])
            other_count = sum(d["count"]             for _, d in sorted_cats[MAX_BLOCKS - 1:])
            other_recl  = sum(d.get("reclaimable_bytes", 0) for _, d in sorted_cats[MAX_BLOCKS - 1:])
            sorted_cats = sorted_cats[:MAX_BLOCKS - 1]
            sorted_cats.append(("Other", {
                "size_bytes": other_size, "reclaimable_bytes": other_recl,
                "count": other_count, "ai_analyzed": 0, "ai_pending": 0, "ai_failed": 0,
                "percentage": 100.0 * other_size / total_bytes,
            }))

        # Build scan label from first non-empty scan path
        scan_label = ""
        if self._donut.parent():
            scan_label = ""  # can be extended later

        self._donut.set_data(sorted_cats, total_bytes, scan_label)
        self._rebuild_cards(sorted_cats)
        self._update_summary_metrics(sorted_cats, total_bytes)

    def _update_summary_metrics(self, sorted_cats: list, total_bytes: int):
        """Refresh the right-panel summary metrics."""
        self._metric_rows["total"].setText(_format_size(total_bytes))
        if sorted_cats:
            top_cat, top_data = sorted_cats[0]
            self._metric_rows["top"].setText(
                f"{tr(top_cat)} · {_format_size(top_data['size_bytes'])}"
            )
        else:
            self._metric_rows["top"].setText("—")
        self._metric_rows["count"].setText(str(len(sorted_cats)))

    def update_skipped(self, entries: list[dict]):
        pass   # overview doesn't show protected block separately

    def mark_viewed(self, category: str):
        pass

    # ── internal ─────────────────────────────────────────────────

    def _rebuild_cards(self, sorted_cats: list):
        # Drop every old card. hide() before deleteLater() is what matters:
        # removeWidget() only takes the card out of the layout, and the widget
        # stays a visible child of the container — parked at its pre-layout
        # 100x58 default in the top-left corner, painting over the new list
        # until the event loop gets round to the deferred delete. Hiding and
        # not unparenting, because setParent(None) promotes the widget to a
        # top-level window and those surface as blank frames over the app.
        self._cards.clear()
        while self._cards_layout.count():
            item = self._cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()

        for cat, data in sorted_cats:
            card = CategoryCardWidget(cat, data, parent=self._cards_container)
            card.clicked.connect(self._on_card_click)
            card.hovered.connect(self._on_card_hover)
            self._cards_layout.addWidget(card)
            self._cards[cat] = card

        self._cards_layout.addStretch()

        # Restore selection highlight
        if self._selected in self._cards:
            self._cards[self._selected].set_selected(True)

    def _on_sector_click(self, category: str):
        self._set_selected(category)
        if self.on_category_click:
            self.on_category_click(category)

    def _on_sector_hover(self, category: str):
        """Donut hover → highlight matching card."""
        for cat, card in self._cards.items():
            card.set_hovered(cat == category and bool(category))

    def _on_card_hover(self, category: str):
        """Card hover → highlight matching donut segment."""
        self._donut.set_hovered(category)

    def _on_card_click(self, category: str):
        self._set_selected(category)
        if self.on_category_click:
            self.on_category_click(category)

    def _set_selected(self, category: str):
        if self._selected and self._selected in self._cards:
            self._cards[self._selected].set_selected(False)
        self._selected = category
        self._donut.set_selected(category)
        if category in self._cards:
            self._cards[category].set_selected(True)




class _PreallocDetailPanel(QWidget):
    """Pre-allocated detail panel — all widgets built once, updated in place.

    Avoids the ~15-20 widget create/destroy cycle that happened on every row
    click in the old _clear_detail_panel() + _build_detail_content() approach.
    """

    # Never let the key column swallow the panel, however long a translation
    # gets — the value is what the user came to read.
    _KEY_COLUMN_MAX = 190

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_key_column()

    def changeEvent(self, event):
        from PySide6.QtCore import QEvent as _QEvent
        super().changeEvent(event)
        if event.type() in (_QEvent.StyleChange, _QEvent.FontChange,
                            _QEvent.LanguageChange):
            self._sync_key_column()

    def _sync_key_column(self):
        """Give every field label the width the widest one actually needs."""
        keys = getattr(self, "_meta_keys", None)
        if not keys:
            return
        try:
            widest = max(
                QFontMetrics(k.font()).horizontalAdvance(k.text()) for k in keys)
        except RuntimeError:
            return
        width = min(self._KEY_COLUMN_MAX, max(88, widest + 6))
        for key in keys:
            key.setFixedWidth(width)

    def __init__(
        self,
        open_cb: Callable,
        copy_cb: Callable,
        recycle_cb: Callable | None = None,
        uninstall_cb: Callable | None = None,
        ask_ai_cb: Callable | None = None,
        ask_ai_file_cb: Callable | None = None,
        parent=None,
        compact: bool = False,
    ):
        super().__init__(parent)
        self._open_cb = open_cb
        self._copy_cb = copy_cb
        self._recycle_cb = recycle_cb
        self._uninstall_cb = uninstall_cb
        self._ask_ai_cb = ask_ai_cb
        self._ask_ai_file_cb = ask_ai_file_cb
        self._compact = compact
        self._current_path: str = ""
        self._current_entity: dict = {}
        self._current_signature: tuple = ()
        self._current_risk: str = "Review"
        self._current_recommendation: str = ""
        self._current_recommendation_accent: str = get_palette().get("text_dim", "#8a9b8f")
        self._ai_has_long_reasoning = False

        p = get_palette()
        faint = p.get("text_faint", "#57685e")

        # Two tabs: "Information" (everything below) and "Files" (paginated
        # per-file browser for grouped/loose entities). The existing content is
        # built into the Information page so behaviour is unchanged.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._tabs = QTabWidget()
        outer.addWidget(self._tabs)

        self._info_page = QWidget()
        root = QVBoxLayout(self._info_page)
        # Breathing room from the tab pane border (the pane itself only adds 4px).
        root.setContentsMargins(12, 10, 12, 12)
        root.setSpacing(10)

        # ── Header row ────────────────────────────────────────────────
        hdr = QHBoxLayout()
        self._name_lbl = QLabel()
        self._name_lbl.setStyleSheet(
            "font-family: 'JetBrains Mono'; font-size: 15px; font-weight: bold;"
        )
        self._name_lbl.setWordWrap(True)
        hdr.addWidget(self._name_lbl, stretch=1)

        self._risk_badge = Badge("REVIEW", "review")
        hdr.addWidget(self._risk_badge)

        self._ai_badge = QLabel()
        self._ai_badge.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 12px;")
        hdr.addWidget(self._ai_badge)
        root.addLayout(hdr)

        self._recommendation_frame = QFrame()
        self._recommendation_frame.setObjectName("FindingRecommendationSection")
        rec_layout = QVBoxLayout(self._recommendation_frame)
        rec_layout.setContentsMargins(0, 4, 0, 2)
        rec_layout.setSpacing(6)

        rec_hdr = QHBoxLayout()
        rec_hdr.setSpacing(8)
        rec_title = QLabel(tr("AI RECOMMENDATIONS"))
        apply_tactical_label(rec_title, font_size=8, letter_spacing=2)
        rec_hdr.addWidget(rec_title)
        self._rec_status_lbl = QLabel(tr("WAITING"))
        self._rec_status_lbl.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 10px;")
        rec_hdr.addWidget(self._rec_status_lbl)
        rec_hdr.addStretch()
        rec_layout.addLayout(rec_hdr)

        self._rec_text_lbl = QLabel(tr("Select a finding to see Vigil's recommendation."))
        self._rec_text_lbl.setWordWrap(True)
        rec_layout.addWidget(self._rec_text_lbl)

        self._rec_evidence_lbl = QLabel("")
        self._rec_evidence_lbl.setWordWrap(True)
        rec_layout.addWidget(self._rec_evidence_lbl)

        # ── Shared label factories ────────────────────────────────────
        _faint_style = "font-family: 'JetBrains Mono'; font-size: 11px; font-weight: 600;"
        _val_style   = "font-family: 'JetBrains Mono'; font-size: 12px;"

        def _mk_key(text: str) -> QLabel:
            # Translate here, once, rather than at seven call sites — every
            # field label in the inspector stayed English in translated
            # builds because each was a bare literal.
            l = QLabel(tr(text))
            l.setStyleSheet(f"{_faint_style} color: {faint};")
            return l

        def _mk_val() -> QLabel:
            l = QLabel()
            l.setStyleSheet(_val_style)
            l.setWordWrap(True)
            return l

        # ── Metadata rows ─────────────────────────────────────────────
        meta_stack = QVBoxLayout()
        meta_stack.setSpacing(6)

        self._cat_key  = _mk_key("CATEGORY:")
        self._cat_val  = _mk_val()
        self._lbl_key  = _mk_key("TYPE:")
        self._lbl_val  = _mk_val()
        self._conf_lbl = QLabel()
        self._conf_lbl.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 11px;")

        # TYPE row is a composite: label text + confidence chip
        lbl_row_w = QWidget()
        lbl_row_l = QHBoxLayout(lbl_row_w)
        lbl_row_l.setContentsMargins(0, 0, 0, 0)
        lbl_row_l.setSpacing(6)
        lbl_row_l.addWidget(self._lbl_val)
        lbl_row_l.addWidget(self._conf_lbl)
        lbl_row_l.addStretch()

        self._path_key  = _mk_key("PATH:")
        # Elided to one line (full path in the tooltip) so a long path can't
        # stretch the inspection panel. ElidedLabel fits the width it is given
        # instead of a fixed character budget, so widening the window reveals
        # more of the path rather than keeping it cut at the same place.
        self._path_val  = ElidedLabel(mode=Qt.ElideMiddle)
        self._path_val.setStyleSheet(_val_style)
        self._size_key  = _mk_key("SIZE:")
        self._size_val  = _mk_val()
        self._items_key = _mk_key("CONTAINS:")
        self._items_val = _mk_val()
        self._activity_key = _mk_key("LAST ACTIVE:")
        self._activity_val = _mk_val()
        self._importance_key = _mk_key("IMPORTANCE:")
        self._importance_val = _mk_val()

        # The key column is one width for every row so the values line up.
        # It used to be a hard 88px, measured against the English labels;
        # "LAST ACTIVE:" becomes "ОСТАННЯ АКТИВНІСТЬ:" and wanted 135px, so
        # the heading was cut. _sync_key_column() measures the real strings
        # once the stylesheet is applied — before that the labels still carry
        # the application default font and come out too narrow.
        self._meta_keys: list[QLabel] = []

        def _meta_row(key_widget: QLabel, value_widget: QWidget) -> QWidget:
            row = QWidget()
            row_l = QHBoxLayout(row)
            row_l.setContentsMargins(0, 0, 0, 0)
            row_l.setSpacing(10)
            key_widget.setFixedWidth(88 if self._compact else 112)
            self._meta_keys.append(key_widget)
            row_l.addWidget(key_widget, 0, Qt.AlignTop)
            row_l.addWidget(value_widget, 1)
            return row

        for k, v in [
            (self._cat_key,        self._cat_val),
            (self._lbl_key,        lbl_row_w),
            (self._path_key,       self._path_val),
            (self._size_key,       self._size_val),
            (self._items_key,      self._items_val),
            (self._activity_key,   self._activity_val),
            (self._importance_key, self._importance_val),
        ]:
            meta_stack.addWidget(_meta_row(k, v))
        meta_stack.addStretch()
        self._sync_key_column()

        left_w = QWidget()
        left_l = QVBoxLayout(left_w)
        left_l.setContentsMargins(0, 0, 0, 0)
        left_l.setSpacing(0)
        left_l.addLayout(meta_stack)
        root.addWidget(left_w)
        root.addWidget(self._recommendation_frame)

        # ── Duplicate locations block ────────────────────────────────
        self._dup_section = QFrame()
        self._dup_section.setObjectName("DuplicateLocationsBlock")
        dup_l = QVBoxLayout(self._dup_section)
        dup_l.setContentsMargins(0, 0, 0, 0)
        dup_l.setSpacing(5)

        dup_hdr = QHBoxLayout()
        dup_hdr.setSpacing(8)
        self._dup_title = QLabel(tr("DUPLICATE COPIES"))
        self._dup_title.setStyleSheet(
            f"font-family: 'Silkscreen', 'JetBrains Mono'; font-size: 8px; "
            f"letter-spacing: 1px; color: {faint};"
        )
        dup_hdr.addWidget(self._dup_title)
        self._dup_meta = QLabel()
        self._dup_meta.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 11px;")
        dup_hdr.addWidget(self._dup_meta)
        dup_hdr.addStretch()
        dup_l.addLayout(dup_hdr)

        # Same rule as the reasoning block: the panel scrolls, this does not.
        # A duplicate group with a dozen copies filled this 126 px box and put
        # its scrollbar alongside the panel's, at every window height.
        self._dup_text = QTextEdit()
        self._dup_text.setReadOnly(True)
        self._dup_text.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._dup_text.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._dup_text.setFrameShape(QFrame.NoFrame)
        self._dup_text.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._dup_text.document().documentLayout().documentSizeChanged.connect(
            lambda _size: self._fit_dup_text_height())
        dup_l.addWidget(self._dup_text)
        self._dup_section.setVisible(False)
        root.addWidget(self._dup_section)

        # ── Contextual reasoning block (full width, below) ────────────
        # Both items in this header row can give way, each with a floor. Making
        # only one shrinkable pinned the other at its minimum: with the caption
        # fixed the badge sat permanently truncated, and with the caption on an
        # Ignored policy it lost every pixel to the row's stretch and vanished.
        self._ai_title = ElidedLabel(tr("CONTEXTUAL REASONING"))
        self._ai_title.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self._ai_title.setMinimumWidth(90)
        self._ai_title.setStyleSheet(
            f"font-family: 'Silkscreen', 'JetBrains Mono'; font-size: 9px; "
            f"letter-spacing: 1px; color: {faint};"
        )

        self._ai_frame = QFrame()
        self._ai_frame.setObjectName("ReasoningBlock")
        ai_frame_layout = QVBoxLayout(self._ai_frame)
        ai_frame_layout.setContentsMargins(0, 0, 0, 0)
        ai_frame_layout.setSpacing(5)

        ai_hdr_row = QHBoxLayout()
        ai_hdr_row.setSpacing(8)
        ai_hdr_row.addWidget(self._ai_title)
        # "Available · Simplified Chinese" — the language makes this the one
        # variable-length item in the row, and a plain label treats its full
        # text as a hard minimum, so it pushed the whole inspector wider than
        # the sidebar and the overflow was cut with no scrollbar to reach it
        # (19 px lost on Ukrainian, 82 px on Simplified Chinese).
        #
        # Preferred keeps its natural width whenever the row has room, and the
        # explicit minimum is what lets it shrink and elide when it does not.
        # Only this one yields: the caption beside it is the section heading
        # and must stay readable.
        self._ai_state_badge = ElidedLabel()
        self._ai_state_badge.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self._ai_state_badge.setMinimumWidth(60)
        self._ai_state_badge.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 11px;")
        ai_hdr_row.addWidget(self._ai_state_badge)
        ai_hdr_row.addStretch()
        # On-demand "Ask AI" — explain just this item even when the bulk AI pass
        # wasn't run. Hidden unless the item has no answer yet (set in update()).
        self._ai_ask_btn = QPushButton(tr("Ask AI"))
        self._ai_ask_btn.setCursor(Qt.PointingHandCursor)
        self._ai_ask_btn.setStyleSheet(ask_ai_button_qss())
        self._ai_ask_btn.setVisible(False)
        self._ai_ask_btn.clicked.connect(self._on_ask_ai_clicked)
        ai_hdr_row.addWidget(self._ai_ask_btn)
        ai_frame_layout.addLayout(ai_hdr_row)

        # A plain container, not a scroll area. This block used to be a 156 px
        # QScrollArea holding a 132 px self-scrolling QTextEdit, inside the
        # sidebar's own page-level scroll area — so once the window was short
        # enough for the page to scroll, a long AI answer put two vertical
        # scrollbars side by side at the right edge. The page scroll is the one
        # that belongs to the panel; nothing nested inside it scrolls now.
        self._ai_body = QWidget()
        ai_layout = QVBoxLayout(self._ai_body)
        ai_layout.setContentsMargins(0, 0, 0, 0)
        ai_layout.setSpacing(4)
        self._ai_content_lbl = QLabel()
        self._ai_content_lbl.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 12px;")
        self._ai_content_lbl.setWordWrap(True)
        self._ai_content_lbl.setVisible(False)
        ai_layout.addWidget(self._ai_content_lbl)

        # Kept a QTextEdit for selectable prose, but it no longer scrolls: it
        # grows to its document and lets the panel scroll instead.
        self._ai_text = QTextEdit()
        self._ai_text.setReadOnly(True)
        self._ai_text.setStyleSheet(
            "QTextEdit { background: transparent; border: none; "
            "font-family: 'JetBrains Mono'; font-size: 12px; }"
        )
        self._ai_text.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._ai_text.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._ai_text.setFrameShape(QFrame.NoFrame)
        self._ai_text.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._ai_text.setVisible(False)
        # Re-fit whenever the document reflows — on new text and on every panel
        # width change, since wrapping decides how tall the answer is.
        self._ai_text.document().documentLayout().documentSizeChanged.connect(
            lambda _size: self._fit_ai_text_height())
        ai_layout.addWidget(self._ai_text)

        self._ai_body.setVisible(False)
        ai_frame_layout.addWidget(self._ai_body)

        self._ai_section = QWidget()
        ai_sec_l = QVBoxLayout(self._ai_section)
        ai_sec_l.setContentsMargins(0, 0, 0, 0)
        ai_sec_l.setSpacing(0)
        ai_sec_l.addWidget(self._ai_frame)
        root.addWidget(self._ai_section)

        # ── Smart action buttons ──────────────────────────────────────
        action_stack = QVBoxLayout()
        action_stack.setSpacing(6)

        self._btn_recycle = QPushButton(tr("Move to Recycle Bin"))
        self._btn_recycle.setObjectName("Primary")
        self._btn_recycle.setCursor(Qt.PointingHandCursor)
        self._btn_recycle.clicked.connect(self._on_recycle)
        action_stack.addWidget(self._btn_recycle)

        self._btn_uninstall = QPushButton(tr("Deep Uninstall"))
        self._btn_uninstall.setObjectName("Subtle")
        self._btn_uninstall.setCursor(Qt.PointingHandCursor)
        self._btn_uninstall.clicked.connect(self._on_uninstall)
        action_stack.addWidget(self._btn_uninstall)

        utility_row = QHBoxLayout()
        utility_row.setSpacing(6)
        self._btn_open = QPushButton(tr("Open in Explorer"))
        self._btn_open.setObjectName("SecondaryAction")
        self._btn_open.setCursor(Qt.PointingHandCursor)
        self._btn_open.clicked.connect(self._on_open)
        utility_row.addWidget(self._btn_open)

        self._btn_copy = QPushButton(tr("Copy path"))
        self._btn_copy.setObjectName("SecondaryAction")
        self._btn_copy.setCursor(Qt.PointingHandCursor)
        self._btn_copy.clicked.connect(self._on_copy)
        utility_row.addWidget(self._btn_copy)

        utility_row.addStretch()
        action_stack.addLayout(utility_row)
        # Stretch BEFORE the action buttons so they always sit at the bottom of
        # the inspection panel regardless of how much (or little) detail the
        # selected entity has. Previously the stretch sat after them, so the
        # buttons floated directly under the content and jumped around as the
        # AI text / recommendation grew or shrank.
        root.addStretch(1)
        root.addLayout(action_stack)

        self._tabs.addTab(self._info_page, tr("Information"))
        self._files_panel = FileListPanel(
            recycle_cb=recycle_cb, ask_ai_file_cb=ask_ai_file_cb)
        self._tabs.addTab(self._files_panel, tr("Files"))
        # Hidden (not just disabled) until an entity with browsable files is
        # selected — a greyed-out empty tab reads as "broken" to users.
        self._tabs.setTabEnabled(1, False)
        self._tabs.setTabVisible(1, False)

        self._apply_block_styles()

    # ── Files tab (paginated per-file browser) ────────────────────────

    _FILES_PER_PAGE = 50

    # ── Slot helpers ──────────────────────────────────────────────────

    def _apply_ai_reasoning_visibility(self):
        """Show exactly one of the two reasoning widgets — never both.

        They are alternatives: the label carries a short rule-based note, the
        text edit carries long AI prose. Showing both stacks a 132 px scrolling
        QTextEdit under a label inside a 156 px scroll area, so the block grows
        its own scrollbar next to the one the text edit already has — the two
        scrollbars reported after clicking Ask AI on an item that was showing a
        default explanation.

        Both visibilities are decided here, from one flag, so a caller cannot
        set half the state and leave the other half stale.
        """
        self._ai_body.setVisible(True)
        self._ai_text.setVisible(self._ai_has_long_reasoning)
        self._ai_content_lbl.setVisible(not self._ai_has_long_reasoning)
        if self._ai_has_long_reasoning:
            self._fit_ai_text_height()

    def _fit_ai_text_height(self):
        """Size the answer box to its text so the panel is the only scroller."""
        self._fit_text_height(self._ai_text)

    def _fit_dup_text_height(self):
        self._fit_text_height(self._dup_text)

    @staticmethod
    def _fit_text_height(edit):
        doc = edit.document()
        doc.setTextWidth(max(1, edit.viewport().width()))
        edit.setFixedHeight(int(doc.size().height()) + 4)

    def _on_open(self):
        if self._current_path:
            self._open_cb(self._current_path)

    def _on_copy(self):
        if self._current_path:
            self._copy_cb(self._current_path)

    def _on_recycle(self):
        if self._current_entity and self._recycle_cb:
            self._recycle_cb(self._current_entity)

    def _on_uninstall(self):
        if self._current_entity and self._uninstall_cb:
            self._uninstall_cb(self._current_entity)

    def _on_ask_ai_clicked(self):
        """User asked to explain just this item. Kick off the request first; if
        it couldn't start (e.g. no model) leave the button as-is. On success,
        show 'Analyzing…' until the answer streams back via the
        finding_updated → populate() signal path."""
        if not (self._current_entity and self._ask_ai_cb):
            return
        reason = self._ask_ai_cb(self._current_entity)
        if reason:
            return  # couldn't queue (handled/announced by the callback)
        # Optimistic in-progress state so the panel reacts instantly. Keep the
        # button visible but disabled and relabelled, so the response is obvious
        # right where the user clicked — not only in the AI block further down,
        # which is often scrolled out of view.
        self._ai_ask_btn.setEnabled(False)
        self._ai_ask_btn.setText(tr("Asking AI…"))
        self._ai_section.setVisible(True)
        self._ai_state_badge.setText(tr("Analyzing"))
        self._ai_state_badge.setStyleSheet(
            f"font-family: 'JetBrains Mono'; font-size: 11px; "
            f"color: {get_palette().get('review', '#d8b46a')};"
        )
        self._ai_content_lbl.setText(tr("Reasoning will appear when analysis finishes."))
        # The previous selection may have had AI prose. Without this reset the
        # stale flag re-showed its answer under the "analyzing" note.
        self._ai_has_long_reasoning = False
        self._apply_ai_reasoning_visibility()

    # ── Contained-files list ──────────────────────────────────────────

    # How many files of a folder the list will draw, and how many entries it
    # will look at to choose them. The scan cap is generous because reading a
    # directory entry is cheap — 1,639 of them with sizes measured 3 ms — while
    # the draw cap exists so the panel does not build thousands of rows.
    _FILES_SHOWN_CAP = 500
    _DIR_SCAN_CAP = 50_000

    def _collect_entity_files(self, entity: dict) -> tuple[list, int, dict]:
        """``(paths to show, how many files there are, sizes already known)``.

        Prefers the stored ``removable_file_paths`` (loose buckets, installers);
        falls back to a live folder listing for content-collection folders so
        photo/video/document groups can also be inspected.

        That listing used to stop dead at the 500th entry, and the tab was then
        labelled "Files (500)" as though that were the whole folder. Two things
        were wrong with it. The count was a fiction — one of these folders holds
        1,639 photos and says so in its own name. And the cut happened in
        ``scandir`` order, which is neither size nor anything else a reader
        would expect, while the list below is sorted biggest-first and so reads
        as "the largest things in here": measured on that folder, **363 of the
        500 largest files were not in the list at all**.

        So the whole directory is read — ``DirEntry.stat()`` is served from the
        directory scan on Windows, so sizes cost almost nothing — and the
        largest ``_FILES_SHOWN_CAP`` are kept. The second return value is the
        real total, which the caller says out loud, and the third hands on the
        sizes this pass already learned — re-measuring them cost 165 ms of the
        panel's own stat budget five milliseconds after they were read, and
        exhausted it, so the last 52 of 500 files ended up recorded as zero
        bytes and sorted to the bottom of a size-ordered list.
        """
        rfp = [p for p in (entity.get("removable_file_paths") or []) if p]
        if rfp:
            return rfp, len(rfp), {}
        if not _is_content_container(entity):
            return [], 0, {}
        path = entity.get("path", "")
        if not path or not os.path.isdir(path):
            return [], 0, {}

        sized: list = []
        try:
            for i, de in enumerate(os.scandir(path)):
                if i >= self._DIR_SCAN_CAP:
                    break
                try:
                    if de.is_file(follow_symlinks=False):
                        st = de.stat()
                        sized.append((st.st_size, st.st_mtime, de.path))
                except OSError:
                    pass
        except OSError:
            pass

        total = len(sized)
        if total > self._FILES_SHOWN_CAP:
            sized.sort(key=lambda row: -row[0])
            sized = sized[:self._FILES_SHOWN_CAP]
        stats = {path_: (size, mtime) for size, mtime, path_ in sized}
        return [path_ for _s, _m, path_ in sized], total, stats

    def _populate_files_section(self, entity: dict):
        """Hand the entity's files to the Files tab, or hide the tab."""
        is_app = _is_application_action_target(entity)
        paths, total, stats = (([], 0, {}) if is_app
                               else self._collect_entity_files(entity))
        count = self._files_panel.load(entity, paths, total=total, stats=stats)

        # A single-file or contentless entity adds no insight — keep Files off.
        if not count:
            self._tabs.setTabEnabled(1, False)
            self._tabs.setTabVisible(1, False)
            self._tabs.setTabText(1, tr("Files"))
            if self._tabs.currentIndex() == 1:
                self._tabs.setCurrentIndex(0)
            return

        self._tabs.setTabEnabled(1, True)
        self._tabs.setTabVisible(1, True)
        # The tab says how many are shown *of* how many there are, rather than
        # presenting a truncated count as the total.
        self._tabs.setTabText(1, tr("Files ({n})").format(n=count)
                              if total <= count else
                              tr("Files ({n} of {total})").format(n=count, total=total))

        # Open on the list for entities whose meaning *is* the list. A loose
        # bucket's Information tab can say little beyond which folder the files
        # were found in, while the per-file view is the reason the row exists —
        # and it was a click away with nothing pointing at it.
        #
        # A folder-backed entity (a photo library, an app) keeps Information:
        # there the folder is the subject and the file list is a detail.
        if _entity_file_group_size(entity) >= 2:
            self._tabs.setCurrentIndex(1)
        elif self._tabs.currentIndex() == 1:
            self._tabs.setCurrentIndex(0)

    def _apply_block_styles(self):
        """Quiet workstation styling for the inspector sections."""
        p = get_palette()
        border = p.get("border", "#213028")
        for frame, obj_name in (
            (self._ai_frame, "ReasoningBlock"),
            (self._dup_section, "DuplicateLocationsBlock"),
        ):
            frame.setStyleSheet(
                f"QFrame#{obj_name} {{ background: transparent; "
                f"border: 1px solid {border}; border-radius: 2px; }}"
            )
        if self._ai_frame.layout():
            self._ai_frame.layout().setContentsMargins(10, 9, 10, 9)
        if self._dup_section.layout():
            self._dup_section.layout().setContentsMargins(10, 9, 10, 9)
        self._name_lbl.setStyleSheet(
            f"font-family: 'JetBrains Mono'; font-size: 15px; font-weight: bold; color: {p.get('text', '#d6e2da')};"
        )
        key_style = (
            f"font-family: 'JetBrains Mono'; font-size: 11px; font-weight: 600; "
            f"color: {p.get('text_dim', '#8a9b8f')};"
        )
        for key in (
            self._cat_key,
            self._lbl_key,
            self._path_key,
            self._size_key,
            self._items_key,
            self._activity_key,
            self._importance_key,
        ):
            key.setStyleSheet(key_style)
        for lbl in (
            self._cat_val,
            self._lbl_val,
            self._path_val,
            self._size_val,
            self._items_val,
            self._activity_val,
            self._importance_val,
        ):
            lbl.setStyleSheet(
                f"font-family: 'JetBrains Mono'; font-size: 12px; color: {p.get('text', '#d6e2da')};"
            )
        self._ai_content_lbl.setStyleSheet(
            f"font-family: 'JetBrains Mono'; font-size: 12px; color: {p.get('text', '#d6e2da')};"
        )
        self._ai_text.setStyleSheet(
            f"QTextEdit {{ background: transparent; border: none; "
            f"font-family: 'JetBrains Mono'; font-size: 12px; color: {p.get('text', '#d6e2da')}; }}"
        )
        self._dup_meta.setStyleSheet(
            f"font-family: 'JetBrains Mono'; font-size: 11px; color: {p.get('text_dim', '#8a9b8f')};"
        )
        self._dup_text.setStyleSheet(
            f"QTextEdit {{ background: transparent; border: none; "
            f"font-family: 'JetBrains Mono'; font-size: 11px; color: {p.get('text', '#d6e2da')}; }}"
        )
        self._btn_recycle.setStyleSheet(
            f"font-size: 11px; padding: 6px 10px; "
            f"color: {p.get('bg_deep', '#080d0a')}; "
            f"background: {p.get('accent', '#7cc596')}; "
            f"border: 1px solid {p.get('accent', '#7cc596')}; border-radius: 2px;"
        )
        self._btn_uninstall.setStyleSheet(
            f"font-size: 11px; padding: 6px 10px; "
            f"color: {p.get('review', '#d8b46a')}; "
            f"background: transparent; "
            f"border: 1px solid {p.get('review', '#d8b46a')}88; border-radius: 2px;"
        )
        utility_style = (
            "QPushButton#SecondaryAction { "
            f"font-size: 11px; padding: 5px 10px; "
            f"color: {p.get('text', '#d6e2da')}; "
            f"background: {p.get('panel_alt', '#18241e')}; "
            f"border: 1px solid {p.get('border_alt', '#2b3d33')}; "
            "border-radius: 2px; } "
            "QPushButton#SecondaryAction:hover { "
            f"background: {p.get('panel_hover', '#1d2c25')}; "
            f"border-color: {p.get('border_hover', '#3a5648')}; }} "
            "QPushButton#SecondaryAction:disabled { "
            f"color: {p.get('text_faint', '#57685e')}; "
            f"background: {p.get('bg_deep', '#080d0a')}; "
            f"border-color: {p.get('border', '#213028')}; }}"
        )
        self._btn_open.setStyleSheet(utility_style)
        self._btn_copy.setStyleSheet(utility_style)
        self._apply_recommendation_card_style(self._current_recommendation_accent)

    def _apply_recommendation_card_style(self, accent: str):
        p = get_palette()
        border = _finding_rgba(accent, 130)
        self._recommendation_frame.setStyleSheet(
            "QFrame#FindingRecommendationSection { background: transparent; border: none; }"
        )
        self._rec_status_lbl.setStyleSheet(
            f"font-family: 'JetBrains Mono'; font-size: 10px; "
            f"color: {accent}; padding: 1px 6px; border: 1px solid {border}; "
            "border-radius: 2px; background: transparent;"
        )
        self._rec_text_lbl.setStyleSheet(
            f"font-size: 12px; font-weight: 650; color: {p.get('text', '#d6e2da')};"
        )
        self._rec_evidence_lbl.setStyleSheet(
            f"font-family: 'JetBrains Mono'; font-size: 10px; "
            f"color: {p.get('text_dim', '#8a9b8f')};"
        )

    def _style_open_as_primary(self, primary: bool):
        """Promote 'Open in Explorer' to the prominent action when no
        whole-folder delete is offered (content containers)."""
        p = get_palette()
        if primary:
            self._btn_open.setStyleSheet(
                "QPushButton { "
                f"font-size: 11px; padding: 6px 10px; "
                f"color: {p.get('bg_deep', '#080d0a')}; "
                f"background: {p.get('accent', '#7cc596')}; "
                f"border: 1px solid {p.get('accent', '#7cc596')}; border-radius: 2px; }}"
            )
        else:
            self._btn_open.setStyleSheet(
                "QPushButton#SecondaryAction { "
                f"font-size: 11px; padding: 5px 10px; "
                f"color: {p.get('text', '#d6e2da')}; "
                f"background: {p.get('panel_alt', '#18241e')}; "
                f"border: 1px solid {p.get('border_alt', '#2b3d33')}; "
                "border-radius: 2px; } "
                "QPushButton#SecondaryAction:hover { "
                f"background: {p.get('panel_hover', '#1d2c25')}; "
                f"border-color: {p.get('border_hover', '#3a5648')}; }}"
            )

    def changeEvent(self, event):
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.StyleChange:
            self._apply_block_styles()
            self._files_panel.schedule_repaint()
        super().changeEvent(event)

    # ── Public API ────────────────────────────────────────────────────

    def populate(self, entity: dict):
        """Update all widgets in-place for the given entity dict."""
        path = entity.get("path", "")
        signature = (
            path,
            normalize_risk(entity.get("risk", "Review")),
            entity.get("ai_status", "none"),
            entity.get("ai_explanation", ""),
            entity.get("ai_error", ""),
            entity.get("recommendation", ""),
            entity.get("dup_reclaimable", 0),
            tuple(entity.get("removable_duplicate_paths") or []),
            entity.get("category", ""),
            entity.get("entity_type", ""),
            entity.get("entity_type_label", ""),
            entity.get("semantic_label", ""),
            entity.get("app_version", ""),
            entity.get("app_publisher", ""),
            entity.get("install_date", ""),
        )
        if path and signature == self._current_signature:
            return
        self._current_path = path
        self._current_entity = dict(entity)
        self._current_signature = signature
        self._ai_has_long_reasoning = False

        name = entity.get("name", "Unknown")
        size = entity.get("size", "—")
        category = entity.get("category", "—")
        risk = normalize_risk(entity.get("risk", "Review"))
        self._current_risk = risk
        file_count = entity.get("file_count", 0)
        folder_count = entity.get("folder_count", 0)
        item_count = file_count + folder_count
        ai_status = entity.get("ai_status", "none")
        ai_explanation = entity.get("ai_explanation", "")
        ai_recommendation = entity.get("recommendation", "")
        self._current_recommendation = ai_recommendation
        semantic_label = entity.get("entity_type_label", entity.get("semantic_label", ""))
        owner_confidence = entity.get("owner_confidence", "none")
        activity_text = _entity_activity_text(entity)
        importance_text = _entity_importance_text(entity)
        contains_text = _entity_contains_text(entity)
        is_duplicate = entity.get("entity_type") == "duplicate_group"
        if is_duplicate:
            self._current_recommendation = _duplicate_recommendation(entity)

        # Header
        self._name_lbl.setText(_duplicate_title(entity) if is_duplicate else name)

        self._risk_badge.set_badge(tr(risk).upper(), _status_variant(risk))

        _pal = get_palette()
        _ai_safe  = _pal.get("safe",       "#7aa88a")
        _ai_warn  = _pal.get("review",     "#c7a66c")
        _ai_risk  = _pal.get("risk",       "#c67a69")
        _ai_idle  = _pal.get("text_faint", "#57685e")
        _ai_map = {
            # U+25D4, not U+25D0 — see _AI_SYMBOL in findings_table_model.
            "ready": ("✓ AI", _ai_safe), "done":      ("✓ AI", _ai_safe),
            "pending": ("◔ AI", _ai_warn), "analyzing": ("◔ AI", _ai_warn),
            "failed": ("✗ AI", _ai_risk), "error":     ("✗ AI", _ai_risk),
            "none": ("— AI", _ai_idle), "disabled": ("⊘ AI", _ai_idle),
        }
        ai_txt, ai_col = _ai_map.get(ai_status, ("—", _ai_idle))
        self._ai_badge.setText(ai_txt)
        self._ai_badge.setStyleSheet(
            f"font-family: 'JetBrains Mono'; font-size: 11px; color: {ai_col};"
        )

        # Info rows
        self._cat_val.setText(tr(category))
        if is_duplicate:
            self._path_val.setText(_norm_path(_duplicate_path_preview(entity)))
        else:
            self._path_val.setText(_norm_path(path) if path else "—")
        self._size_val.setText(size)
        self._items_val.setText(
            contains_text or tr("{n:,} items", n=item_count))
        # For duplicates the dedicated DUPLICATE LOCATIONS block below already
        # spells out copies/locations, so the CONTAINS row is pure repetition.
        items_row = self._items_val.parentWidget()
        if items_row is not None:
            items_row.setVisible(not is_duplicate)
        self._activity_val.setText(activity_text)
        self._importance_val.setText(importance_text)
        rec_status, rec_text, rec_evidence, rec_accent = _finding_recommendation(entity)
        self._current_recommendation_accent = rec_accent
        self._rec_status_lbl.setText(rec_status)
        self._rec_text_lbl.setText(rec_text)
        self._rec_evidence_lbl.setText(rec_evidence)
        self._apply_recommendation_card_style(rec_accent)

        # LABEL row — show only when a semantic label is present
        has_label = bool(semantic_label)
        self._lbl_key.setVisible(has_label)
        # ENTITY_TYPES values are in the locale files; they only reach them
        # through tr(), and this call site was passing the raw English.
        self._lbl_val.setText(tr(semantic_label))
        lbl_row_w = self._lbl_val.parentWidget()
        if lbl_row_w:
            lbl_row_w.setVisible(has_label)

        if has_label:
            dim = get_palette().get("text_dim", "#8a9b8f")
            _conf_colors = {
                "exact":     get_palette().get("safe",       "#7cc596"),
                "probable":  get_palette().get("review",     "#d8b46a"),
                "heuristic": dim,
            }
            conf_visible = owner_confidence not in ("", "none", None)
            cc = _conf_colors.get(owner_confidence, dim)
            self._conf_lbl.setVisible(conf_visible)
            self._conf_lbl.setText(owner_confidence if conf_visible else "")
            self._conf_lbl.setStyleSheet(
                f"font-family: 'JetBrains Mono'; font-size: 11px; color: {cc};"
            )

        # ── Reasoning section ─────────────────────────────────────────
        # Duplicates always carry a plain-language explanation (rule-based)
        # when the model hasn't produced its own prose, so the panel reads as
        # reasoning rather than a second copy of the path list.
        has_ai_prose = ai_status in ("ready", "done") and bool(ai_explanation)
        is_container = _is_content_container(entity)
        # On-demand: offer "Ask AI" when this item has no answer yet (or failed)
        # and an explainer is wired. This lets the user get reasoning for a
        # single item even if the bulk AI pass never ran.
        can_ask_ai = (
            self._ask_ai_cb is not None
            and not has_ai_prose
            and ai_status not in ("pending", "analyzing")
        )
        show_ai = (
            bool(ai_explanation or ai_status in ("pending", "analyzing", "failed", "error"))
            or is_duplicate
            or is_container
            or can_ask_ai
        )
        self._ai_section.setVisible(show_ai)
        self._ai_ask_btn.setText(tr("Ask AI"))
        self._ai_ask_btn.setVisible(can_ask_ai)
        self._ai_ask_btn.setEnabled(True)
        if show_ai:
            # Quiet, integrated reasoning block — no accent glow.
            self._apply_block_styles()
            if ai_status in ("pending", "analyzing"):
                self._ai_has_long_reasoning = False
                self._ai_state_badge.setText(tr("Analyzing"))
                self._ai_state_badge.setStyleSheet(
                    f"font-family: 'JetBrains Mono'; font-size: 11px; color: {get_palette().get('review', '#d8b46a')};"
                )
                self._ai_content_lbl.setText(tr("Reasoning will appear when analysis finishes."))
                self._ai_content_lbl.setStyleSheet(
                    f"font-family: 'JetBrains Mono'; font-size: 12px; "
                    f"color: {get_palette().get('review', '#d8b46a')};"
                )
                self._apply_ai_reasoning_visibility()
            elif has_ai_prose:
                self._ai_has_long_reasoning = True
                # Signpost the language so a Ukrainian explanation under an
                # English UI reads as intentional, not a glitch.
                ai_lang = (entity.get("ai_language") or "").strip()
                self._ai_state_badge.setText(
                    tr("Available · {lang}").format(lang=ai_lang) if ai_lang
                    else tr("Available")
                )
                self._ai_state_badge.setStyleSheet(
                    f"font-family: 'JetBrains Mono'; font-size: 11px; color: {get_palette().get('text_dim', '#8a9b8f')};"
                )
                self._ai_text.setPlainText(ai_explanation)
                self._apply_ai_reasoning_visibility()
            elif is_duplicate:
                # Rule-based duplicate explanation in place of AI prose.
                self._ai_has_long_reasoning = False
                self._ai_state_badge.setText(tr("Summary"))
                self._ai_state_badge.setStyleSheet(
                    f"font-family: 'JetBrains Mono'; font-size: 11px; color: {get_palette().get('text_dim', '#8a9b8f')};"
                )
                self._ai_content_lbl.setText(_duplicate_explanation(entity))
                self._ai_content_lbl.setStyleSheet(
                    f"font-family: 'JetBrains Mono'; font-size: 12px; "
                    f"color: {get_palette().get('text', '#d6e2da')};"
                )
                self._apply_ai_reasoning_visibility()
            elif is_container:
                # Rule-based help for personal/mixed containers — what it holds
                # and how to reclaim space without deleting the whole folder.
                self._ai_has_long_reasoning = False
                self._ai_state_badge.setText(tr("Summary"))
                self._ai_state_badge.setStyleSheet(
                    f"font-family: 'JetBrains Mono'; font-size: 11px; color: {get_palette().get('text_dim', '#8a9b8f')};"
                )
                self._ai_content_lbl.setText(_container_explanation(entity))
                self._ai_content_lbl.setStyleSheet(
                    f"font-family: 'JetBrains Mono'; font-size: 12px; "
                    f"color: {get_palette().get('text', '#d6e2da')};"
                )
                self._apply_ai_reasoning_visibility()
            elif ai_status in ("failed", "error"):
                self._ai_has_long_reasoning = False
                self._ai_state_badge.setText(tr("Unavailable"))
                self._ai_state_badge.setStyleSheet(
                    f"font-family: 'JetBrains Mono'; font-size: 11px; color: {get_palette().get('risk', '#d68a78')};"
                )
                ai_error = entity.get("ai_error", "Unknown error")
                self._ai_content_lbl.setText(
                    tr("Reasoning is not available right now: {error}").format(error=ai_error)
                )
                self._ai_content_lbl.setStyleSheet(
                    f"font-family: 'JetBrains Mono'; font-size: 12px; "
                    f"color: {get_palette().get('risk', '#d68a78')}; "
                )
                self._apply_ai_reasoning_visibility()
            elif can_ask_ai:
                # No reasoning yet — invite the user to ask about this item.
                self._ai_has_long_reasoning = False
                self._ai_state_badge.setText(tr("Not analyzed"))
                self._ai_state_badge.setStyleSheet(
                    f"font-family: 'JetBrains Mono'; font-size: 11px; color: {get_palette().get('text_dim', '#8a9b8f')};"
                )
                self._ai_content_lbl.setText(
                    tr("No AI reasoning yet — click Ask AI to explain this item.")
                )
                self._ai_content_lbl.setStyleSheet(
                    f"font-family: 'JetBrains Mono'; font-size: 12px; "
                    f"color: {get_palette().get('text_dim', '#8a9b8f')};"
                )
                self._apply_ai_reasoning_visibility()
            else:
                self._ai_state_badge.setText("")
                self._ai_body.setVisible(False)
        else:
            self._ai_state_badge.setText("")
            self._ai_body.setVisible(False)

        if is_duplicate:
            locations = _duplicate_locations(entity)
            self._dup_meta.setText(tr("// {n} copies",
                                      n=len(locations) or _duplicate_copy_count(entity)))
            self._dup_text.setPlainText(_duplicate_locations_text(entity))
            self._dup_section.setVisible(True)
        else:
            self._dup_section.setVisible(False)

        # Re-apply compact local styling after badge text/state updates.
        self._apply_block_styles()

        # Buttons — actionability decides which destructive action is even
        # offered. Content containers (personal/mixed) never expose a
        # whole-folder Recycle; Open in Explorer becomes the primary action.
        has_path = bool(path and path != "—")
        is_app = _is_application_action_target(entity)
        has_uninstaller = _has_uninstaller(entity)
        actionability = _entity_actionability(entity)
        allow_recycle = (
            has_path and self._recycle_cb is not None
            and actionability != "protected"
        )
        # For an application, Deep Uninstall (the app's own uninstaller) is the
        # correct removal — recycling the install tree leaves registry/state
        # behind. So when a real uninstaller exists, it's the ONLY destructive
        # action; recycle is offered only as a fallback when none is registered.
        if is_app and has_uninstaller:
            allow_recycle = False
        self._btn_recycle.setVisible(allow_recycle)
        self._btn_recycle.setEnabled(allow_recycle)
        # Only offer Deep Uninstall when an uninstaller actually exists. Plenty
        # of Program Files folders (WSL, gstreamer, Fortinet, vendor
        # components…) register no uninstall command.
        #
        # This used to stay visible-but-disabled, carrying the reason in a
        # tooltip. Reported as confusing, and rightly: a tooltip is invisible
        # until you hover and unreachable from the keyboard, so what the user
        # actually sees is a button for an action the app cannot perform. Don't
        # offer the affordance unless it works — recycle stays available as the
        # fallback, because allow_recycle is only suppressed when a real
        # uninstaller exists.
        can_uninstall = (is_app and has_uninstaller
                         and self._uninstall_cb is not None)
        self._btn_uninstall.setVisible(can_uninstall)
        self._btn_uninstall.setEnabled(can_uninstall)
        self._btn_open.setEnabled(has_path)
        self._btn_copy.setEnabled(has_path)
        # When no whole-folder delete is offered, make Open the prominent action.
        self._style_open_as_primary(is_container and not allow_recycle and not is_app)

        # Expandable per-file list for grouped/loose entities.
        self._populate_files_section(entity)


class RightSidebar(QFrame):
    """Persistent right-side inspector for selected finding details."""

    def __init__(
        self,
        open_cb: Callable,
        copy_cb: Callable,
        recycle_cb: Callable | None = None,
        uninstall_cb: Callable | None = None,
        ask_ai_cb: Callable | None = None,
        ask_ai_file_cb: Callable | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("RightSidebar")
        self.setMinimumWidth(280)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)

        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        title = QLabel(tr("ENTITY INSPECTION"))
        apply_tactical_label(title, font_size=8, letter_spacing=1)
        hdr.addWidget(title)
        self._meta = QLabel(tr("// details"))
        self._meta.setObjectName("Muted")
        self._meta.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 9px;")
        hdr.addWidget(self._meta)
        hdr.addStretch()
        layout.addLayout(hdr)

        self._sep = QFrame()
        self._sep.setFixedHeight(1)
        layout.addWidget(self._sep)

        self._stack = QStackedWidget()
        self._stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self._empty = QLabel(tr("Select a finding to inspect its metadata, risk, and AI reasoning."))
        self._empty.setAlignment(Qt.AlignCenter)
        self._empty.setWordWrap(True)
        self._empty.setObjectName("Muted")
        self._empty.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 12px; padding: 22px;")
        self._stack.addWidget(self._empty)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setStyleSheet("border: none; background: transparent;")

        self.detail_widget = _PreallocDetailPanel(
            open_cb=open_cb,
            copy_cb=copy_cb,
            recycle_cb=recycle_cb,
            uninstall_cb=uninstall_cb,
            ask_ai_cb=ask_ai_cb,
            ask_ai_file_cb=ask_ai_file_cb,
            compact=True,
        )
        self.detail_widget.setStyleSheet("background: transparent; border: none;")
        self.detail_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self._scroll.setWidget(self.detail_widget)
        self._stack.addWidget(self._scroll)

        layout.addWidget(self._stack, stretch=1)
        self.apply_style()
        self.clear()

    def apply_style(self):
        p = get_palette()
        detail_bg = p.get("panel_alt", "#18241e")
        border = p.get("border_alt", "#2b3d33")
        faint = p.get("text_faint", "#57685e")
        line = QColor(p.get("border", "#213028"))
        line.setAlpha(58)
        line_rgba = f"rgba({line.red()}, {line.green()}, {line.blue()}, {line.alpha()})"
        self.setStyleSheet(
            f"QFrame#RightSidebar {{ background: {detail_bg}; "
            f"border: 1px solid {border}; border-radius: 2px; }}"
        )
        self._sep.setStyleSheet(f"background: {line_rgba}; border: none;")
        self._empty.setStyleSheet(
            f"font-family: 'JetBrains Mono'; font-size: 12px; "
            f"color: {faint}; padding: 22px;"
        )
        self.detail_widget.setStyleSheet(f"background: {detail_bg}; border: none;")
        self.detail_widget._apply_block_styles()

    def populate(self, entity: dict):
        self.detail_widget.populate(entity)
        self._meta.setText(tr("// selected"))
        self._stack.setCurrentWidget(self._scroll)

    def clear(self):
        self.detail_widget._current_path = ""
        self.detail_widget._current_entity = {}
        self.detail_widget._current_signature = ()
        self._meta.setText(tr("// details"))
        self._stack.setCurrentWidget(self._empty)


# Was a byte-identical copy of Quick Cleanup's; both now share one widget.
_FindingSelectionCheckBox = TacticalCheckBox


class FindingsEntityRow(QFrame):
    """Softer entity row for the category inspection list.

    Doubles as the header of an app group — see rebind(). The header carries
    the group's totals and an expand chevron; its members render as the same
    widget, indented.
    """

    clicked = Signal(int)
    check_toggled = Signal(int, bool)
    group_toggled = Signal(str)      # group key, emitted by a header row

    def __init__(self, source_row: int, entity: dict, checked: bool = False, parent=None):
        super().__init__(parent)
        self._source_row = source_row
        self._entity = entity
        self._selected = False
        self._hovered = False
        self.setObjectName("FindingEntityRow")
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        # Two-line row with enough vertical air for quick scanning.
        self.setMinimumHeight(52)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 14, 8)
        layout.setSpacing(12)

        self._check_btn = _FindingSelectionCheckBox()
        self._check_btn.clicked.connect(self._on_check_clicked)
        layout.addWidget(self._check_btn, alignment=Qt.AlignVCenter)

        # Expand/collapse affordance — only shown on a group header row.
        self._group: dict | None = None
        self._depth: int = 0
        # Always present, never hidden: hiding it collapsed the column and
        # pushed ungrouped rows 22px left of grouped ones, which read as
        # broken alignment rather than hierarchy. Blank text reserves the
        # space instead.
        self._chevron = QLabel("")
        self._chevron.setFixedWidth(14)
        self._chevron.setAlignment(Qt.AlignCenter)
        self._chevron.setCursor(Qt.PointingHandCursor)
        layout.addWidget(self._chevron, alignment=Qt.AlignVCenter)

        center = QVBoxLayout()
        center.setSpacing(3)

        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        # Elided, not plain: both of these already carried an Ignored size
        # policy so the row would never force the list wider, which meant a
        # plain QLabel simply had its tail cut off — no ellipsis, no tooltip.
        # At the 1100px minimum window the meta line lost a third of itself.
        self._name_lbl = ElidedLabel(entity.get("name", "Unknown"))
        self._name_lbl.setStyleSheet("font-size: 14px; font-weight: 760;")
        title_row.addWidget(self._name_lbl, stretch=1)

        _risk0 = entity.get("risk", "Review")
        # tr(): the badge sat next to filter chips that WERE translated, so
        # one screen showed "REVIEW" and "À vérifier" for the same thing.
        self._risk_badge = Badge(tr(_risk0), _status_variant(_risk0))
        title_row.addWidget(self._risk_badge, alignment=Qt.AlignVCenter)
        center.addLayout(title_row)

        self._meta_lbl = ElidedLabel()
        self._meta_lbl.setObjectName("Muted")
        self._meta_lbl.setStyleSheet(
            "font-family: 'JetBrains Mono'; font-size: 11px;"
        )
        center.addWidget(self._meta_lbl)

        # Path label kept as a hidden attribute so update_entity can still
        # populate it for selection / preview, but it's no longer added to
        # the row layout — path is shown in the Entity Inspection panel.
        self._path_lbl = QLabel(entity.get("path", "—"))
        self._path_lbl.setObjectName("Dim")
        self._path_lbl.setVisible(False)
        layout.addLayout(center, stretch=1)

        right = QVBoxLayout()
        right.setSpacing(2)
        right.setAlignment(Qt.AlignVCenter | Qt.AlignRight)

        self._size_lbl = QLabel(entity.get("size", "—"))
        self._size_lbl.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 13px; font-weight: 600;")
        self._size_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._size_lbl.setMinimumWidth(110)
        right.addWidget(self._size_lbl)

        self._aux_lbl = QLabel()
        self._aux_lbl.setObjectName("Dim")
        self._aux_lbl.setStyleSheet(
            "font-family: 'JetBrains Mono'; font-size: 10px;"
        )
        self._aux_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._aux_lbl.setMinimumWidth(110)
        right.addWidget(self._aux_lbl)

        self._ai_lbl = QLabel()
        self._ai_lbl.setObjectName("Muted")
        self._ai_lbl.setStyleSheet(
            "font-family: 'JetBrains Mono'; font-size: 10px;"
        )
        self._ai_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._ai_lbl.setMinimumWidth(110)
        right.addWidget(self._ai_lbl)
        layout.addLayout(right)

        self.update_entity(entity, checked)
        self._apply_style()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and not self._check_btn.geometry().contains(event.position().toPoint()):
            if self.is_group_header():
                # The whole header toggles, not just the chevron — a 10px
                # target for the primary action of the row would be unkind.
                self.group_toggled.emit(self.group_key())
            else:
                self.clicked.emit(self._source_row)
        super().mousePressEvent(event)

    def enterEvent(self, event):
        self._hovered = True
        self._apply_style()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._apply_style()
        super().leaveEvent(event)

    def _on_check_clicked(self, checked: bool):
        self.check_toggled.emit(self._source_row, checked)

    def set_selected(self, selected: bool):
        self._selected = selected
        self._apply_style()

    def set_checked(self, checked: bool):
        """Lightweight checkbox-only update — no text or style recompute.

        Used on selection changes so toggling one checkbox doesn't re-render
        every row's labels.
        """
        self._check_btn.blockSignals(True)
        self._check_btn.setChecked(checked)
        self._check_btn.blockSignals(False)
        self._check_btn.update()

    def rebind(self, source_row: int, entity: dict, checked: bool,
               *, depth: int = 0, group: dict | None = None):
        """Repoint a pooled row at a new entity instead of recreating it.

        ``depth`` indents a row that belongs to a group; ``group`` turns the
        row into that group's header, carrying the expand state and the
        aggregate figures. One widget serves both jobs on purpose: headers and
        members share a single ordered pool, so the visual order is simply the
        order rows are bound in. Two pools could not interleave, because a
        QVBoxLayout fixes widget order when they are inserted.
        """
        self._source_row = source_row
        self._selected = False
        self._hovered = False
        self._group = group
        self._depth = depth
        self.update_entity(entity, checked)
        self._apply_group_chrome()

    def _apply_group_chrome(self):
        """Show the expand chevron and indent for grouped rows."""
        group = getattr(self, "_group", None)
        depth = getattr(self, "_depth", 0)
        lay = self.layout()
        lay.setContentsMargins(12 + depth * 22, 8, 14, 8)
        accent = get_palette().get("accent", "#7cc596")
        if group is None:
            self._chevron.setText("")
            self._chevron.setToolTip("")
            return
        # Large enough and coloured enough to read as a control. At 9px in the
        # body colour it was a speck, and the one affordance the whole
        # grouping feature depends on went unnoticed.
        # U+25BC/U+25B6, not the "small triangle" pair U+25BE/U+25B8: the
        # small variants render at roughly half height whatever the font size,
        # which is why the affordance stayed invisible after being enlarged.
        self._chevron.setStyleSheet(
            f"font-size: 10px; color: {accent}; background: transparent;")
        self._chevron.setText("▼" if group.get("expanded") else "▶")
        self._chevron.setToolTip(
            tr("Hide the {n} items inside", n=group.get("count", 0))
            if group.get("expanded")
            else tr("Show the {n} items inside", n=group.get("count", 0)))

    def is_group_header(self) -> bool:
        return getattr(self, "_group", None) is not None

    def group_key(self) -> str:
        group = getattr(self, "_group", None)
        return group.get("key", "") if group else ""

    def update_entity(self, entity: dict, checked: bool):
        self._entity = entity
        risk = entity.get("risk", "Review")
        is_duplicate = entity.get("entity_type") == "duplicate_group"
        self._name_lbl.setText(_duplicate_title(entity) if is_duplicate else entity.get("name", "Unknown"))
        self._risk_badge.set_badge(tr(risk), _status_variant(risk))
        if is_duplicate:
            self._meta_lbl.setText(_duplicate_row_meta(entity))
            self._path_lbl.setText(_duplicate_path_preview(entity))
        else:
            # _entity_contains_text() already returns the type label at the
            # end (e.g. "956 files · 44 folders · Python Virtual Environment")
            # — adding 'semantic' on top duplicated the type name.
            self._meta_lbl.setText(_entity_contains_text(entity))
            self._path_lbl.setText(entity.get("path", "—"))
        self._size_lbl.setText(entity.get("size", "—"))
        self._aux_lbl.setText(_entity_activity_text(entity))
        ai_text = {
            "ready": tr("AI reviewed"),
            "done": tr("AI reviewed"),
            "pending": tr("AI queued"),
            "analyzing": tr("AI analyzing"),
            "failed": tr("AI unavailable"),
            "error": tr("AI unavailable"),
            "disabled": tr("AI disabled"),
        }.get(entity.get("ai_status", "none"), "")
        self._ai_lbl.setText(ai_text)
        # Only system-protected entities can never be selected. Review and
        # "uncertain" (Unknown/mixed) items ARE selectable — deleting them just
        # requires the explicit acknowledgment in the cleanup dialog.
        selectable = _entity_actionability(entity) != "protected"
        self._check_btn.setEnabled(selectable)
        self._check_btn.blockSignals(True)
        self._check_btn.setChecked(checked and selectable)
        self._check_btn.blockSignals(False)
        self._apply_check_style()

    def _apply_check_style(self):
        self._check_btn.update()

    def _apply_style(self):
        p = get_palette()
        primary = p.get("text", "#d6e2da")
        meta = p.get("text_dim", "#8a9b8f")
        aux = p.get("text_faint", "#57685e")
        if self._selected:
            accent = p.get("accent", "#7cc596")
            bg = p.get("accent_soft", "#1b2e22")
            border = p.get("border_hover", "#3a5648")
            self.setStyleSheet(
                f"QFrame#FindingEntityRow {{ background: {bg}; "
                f"border-left: 3px solid {accent}; "
                f"border-top: 1px solid {border}; "
                f"border-bottom: 1px solid {border}; "
                f"border-right: 1px solid {border}; }}"
            )
            self._name_lbl.setStyleSheet(
                f"font-size: 14px; font-weight: 800; color: {primary};"
            )
        elif self._hovered:
            bg = p.get("panel_hover", "#1d2c25")
            self.setStyleSheet(
                f"QFrame#FindingEntityRow {{ background: {bg}; "
                f"border: 1px solid {p.get('border', '#213028')}; }}"
            )
            self._name_lbl.setStyleSheet(
                f"font-size: 14px; font-weight: 760; color: {primary};"
            )
        else:
            bg = "transparent"
            self.setStyleSheet(
                f"QFrame#FindingEntityRow {{ background: {bg}; border: none; }}"
            )
            self._name_lbl.setStyleSheet(
                f"font-size: 14px; font-weight: 760; color: {primary};"
            )
        self._meta_lbl.setStyleSheet(
            f"font-family: 'JetBrains Mono'; font-size: 11px; color: {meta};"
        )
        self._aux_lbl.setStyleSheet(
            f"font-family: 'JetBrains Mono'; font-size: 10px; color: {aux};"
        )
        self._ai_lbl.setStyleSheet(
            f"font-family: 'JetBrains Mono'; font-size: 10px; color: {aux};"
        )
        self._apply_check_style()


def _group_risk(group: dict) -> str:
    """The most cautious risk in a group — a header must never look safer
    than the least safe thing folded under it."""
    entities = list(group.get("members") or [])
    if group.get("root") is not None:
        entities.append(group["root"])
    worst, worst_idx = "Safe", -1
    for e in entities:
        risk = normalize_risk(e.get("risk", "Review"))
        idx = _GROUP_RISK_ORDER.get(risk, 0)
        if idx > worst_idx:
            worst, worst_idx = risk, idx
    return worst


# Protected outranks everything: a group holding one protected item must say so.
_GROUP_RISK_ORDER = {"Safe": 0, "Optional": 1, "Review": 2, "Protected": 3}


class FolderTreeView(QFrame):
    """Browse the scan by folder instead of by classification.

    Every other view answers "what is this?", which is a judgement and can be
    wrong: WSL was filed under Virtual Machines because two .vhdx images
    outweighed 619 DLLs, and Discord under Media. This answers "where is it?",
    which is never a judgement — so when a label is wrong the user is not
    stuck. It is also the view that makes "what is eating my disk" answerable
    by following the biggest number down, the way every disk tool works.
    """

    entity_activated = Signal(dict)     # user picked a folder that is a finding

    def __init__(self, parent=None, on_back: Callable = None):
        super().__init__(parent)
        self.on_back = on_back
        self.setObjectName("Panel")
        self._root: PathNode | None = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        nav = QHBoxLayout()
        nav.setSpacing(12)
        back = QPushButton(tr("← Back to Overview"))
        back.setObjectName("Subtle")
        back.setStyleSheet("font-size: 11px; padding: 6px 12px;")
        back.setCursor(Qt.PointingHandCursor)
        back.clicked.connect(lambda: self.on_back() if self.on_back else None)
        nav.addWidget(back)

        title = QLabel(tr("BY FOLDER"))
        apply_tactical_label(title, font_size=14, letter_spacing=3)
        nav.addWidget(title)

        self._summary_lbl = QLabel("")
        self._summary_lbl.setObjectName("Muted")
        self._summary_lbl.setStyleSheet(
            "font-family: 'JetBrains Mono'; font-size: 11px;")
        nav.addWidget(self._summary_lbl)
        nav.addStretch()
        layout.addLayout(nav)

        hint = QLabel(tr("Sizes include everything inside a folder. "
                         "Largest first, so the space is always the top row."))
        hint.setObjectName("Dim")
        hint.setStyleSheet("font-size: 11px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._tree = QTreeWidget()
        self._tree.setObjectName("FolderTree")
        self._tree.setColumnCount(3)
        self._tree.setHeaderLabels([tr("FOLDER"), tr("SIZE"), tr("ITEMS")])
        self._tree.setRootIsDecorated(True)
        self._tree.setUniformRowHeights(True)
        self._tree.setAlternatingRowColors(False)
        self._tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self._tree.setColumnWidth(0, 620)
        self._tree.header().setStretchLastSection(False)
        self._tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self._tree.header().setSectionResizeMode(1, QHeaderView.Fixed)
        self._tree.header().setSectionResizeMode(2, QHeaderView.Fixed)
        self._tree.setColumnWidth(1, 110)
        self._tree.setColumnWidth(2, 90)
        for col, align in ((1, Qt.AlignRight), (2, Qt.AlignRight)):
            item = self._tree.headerItem()
            item.setTextAlignment(col, align | Qt.AlignVCenter)
        self._tree.itemExpanded.connect(self._on_expanded)
        self._tree.itemActivated.connect(self._on_activated)
        self._tree.itemClicked.connect(self._on_activated)
        install_header_fit(self._tree)
        layout.addWidget(self._tree, stretch=1)
        self._apply_style()

    def _apply_style(self):
        p = get_palette()
        self._tree.setStyleSheet(
            f"QTreeWidget#FolderTree {{ background: {p.get('panel_alt', '#18241e')}; "
            f"border: 1px solid {p.get('border', '#213028')}; "
            f"font-family: 'JetBrains Mono'; font-size: 11px; "
            f"selection-background-color: {p.get('accent_soft', '#1b2e22')}; "
            f"selection-color: {p.get('text', '#d6e2da')}; }} "
            f"QTreeWidget#FolderTree::item {{ padding: 5px 4px; border: none; }} "
            f"QHeaderView::section {{ background: {p.get('panel', '#141d18')}; "
            f"color: {p.get('text_faint', '#57685e')}; border: none; "
            f"border-bottom: 1px solid {p.get('border', '#213028')}; "
            f"padding: 8px 6px; font-family: 'Silkscreen', 'JetBrains Mono'; "
            f"font-size: 8px; letter-spacing: 1px; }}"
        )

    # ── population ────────────────────────────────────────────────

    def set_entities(self, entities: list):
        """Rebuild the tree. Only the top level is materialised up front —
        a full C:/ scan is ~1,200 entities and several thousand folders, and
        building every QTreeWidgetItem eagerly stalls the screen."""
        self._root = collapse_single_child_chains(build_tree(entities))
        self._tree.clear()
        self._summary_lbl.setText(
            tr("// {size} across {n:,} items",
               size=_format_size(self._root.size_bytes),
               n=self._root.entity_count))
        for node in self._root.sorted_children():
            self._tree.addTopLevelItem(self._make_item(node))

    def _make_item(self, node: PathNode) -> QTreeWidgetItem:
        item = QTreeWidgetItem([node.name,
                                _format_size(node.size_bytes),
                                f"{node.entity_count:,}"])
        item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
        item.setTextAlignment(2, Qt.AlignRight | Qt.AlignVCenter)
        item.setData(0, Qt.UserRole, node)
        item.setToolTip(0, node.path)
        if node.entity is not None:
            risk = normalize_risk(node.entity.get("risk", "Review"))
            item.setForeground(0, QBrush(QColor(_risk_fg(risk))))
        if node.children:
            # A placeholder child gives the expand arrow without building the
            # subtree; _on_expanded swaps it for the real rows on first open.
            item.addChild(QTreeWidgetItem(["…"]))
        return item

    def _on_expanded(self, item: QTreeWidgetItem):
        node = item.data(0, Qt.UserRole)
        if node is None:
            return
        if item.childCount() == 1 and item.child(0).data(0, Qt.UserRole) is None:
            item.takeChildren()
            for child in node.sorted_children():
                item.addChild(self._make_item(child))

    def _on_activated(self, item: QTreeWidgetItem, _column: int = 0):
        node = item.data(0, Qt.UserRole)
        if node is not None and node.entity is not None:
            self.entity_activated.emit(node.entity)


class CategoryDetailView(QFrame):
    """Category drill-down using a calmer list with right-side inspection."""

    def __init__(self, parent=None, on_back: Callable = None):
        super().__init__(parent)
        self.on_back = on_back
        self.category: Optional[str] = None
        self.entities: list = []
        self._scan_state = None
        self._selected_path: str = ""
        self._row_widgets: dict[str, FindingsEntityRow] = {}
        self._row_pool: list[FindingsEntityRow] = []
        self._expanded_groups: set[str] = set()
        self._app_index_cache: dict | None = None

        self.setObjectName("Panel")
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        # Navigation header
        nav = QHBoxLayout()
        nav.setSpacing(12)

        back_btn = QPushButton(tr("← Back to Overview"))
        back_btn.setObjectName("Subtle")
        back_btn.setStyleSheet("font-size: 11px; padding: 6px 12px;")
        back_btn.setCursor(Qt.PointingHandCursor)
        back_btn.clicked.connect(lambda: self.on_back() if self.on_back else None)
        nav.addWidget(back_btn)

        self._title_lbl = QLabel(tr("CATEGORY"))
        self._title_lbl.setStyleSheet(
            "font-family: 'Silkscreen', 'JetBrains Mono'; font-size: 14px; letter-spacing: 3px;"
        )
        nav.addWidget(self._title_lbl)

        self._stats_lbl = QLabel(tr("// 0 items"))
        self._stats_lbl.setObjectName("Muted")
        self._stats_lbl.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 10px;")
        nav.addWidget(self._stats_lbl)

        nav.addStretch()

        # Sort dropdown
        sort_lbl = QLabel(tr("SORT:"))
        sort_lbl.setObjectName("Muted")
        sort_lbl.setStyleSheet("font-family: 'Silkscreen', 'JetBrains Mono'; font-size: 9px;")
        nav.addWidget(sort_lbl)

        # A dropdown (not a cycling button) so the user can see every sort
        # option and what they're choosing.
        self._sort_combo = TacticalComboBox()
        for key, label in FindingsFilterProxy.SORT_KEYS:
            self._sort_combo.addItem(tr(label), key)
        self._sort_combo.setCurrentIndex(0)
        self._sort_combo.apply_reference_style(get_palette(), compact=True)
        self._sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        nav.addWidget(self._sort_combo)

        layout.addLayout(nav)

        # AI status bar
        ai_bar = QHBoxLayout()
        ai_bar.setSpacing(16)

        self._ai_summary_lbl = QLabel(tr("AI queue idle"))
        self._ai_summary_lbl.setStyleSheet(
            f"font-family: 'JetBrains Mono'; font-size: 11px; color: {get_palette().get('text_faint', '#57685e')};"
        )
        ai_bar.addWidget(self._ai_summary_lbl)
        ai_bar.addStretch()

        layout.addLayout(ai_bar)

        # Filter/search
        filter_row = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText(tr("Search…"))
        self._search.setFixedWidth(280)
        self._search.setStyleSheet(
            "QLineEdit { padding: 6px 10px; border-radius: 2px; }"
        )
        filter_row.addWidget(self._search)

        filter_row.addSpacing(4)

        # Risk filter chips — uniform tactical labels, theme-aware accents.
        # "All" is a reset rather than a fifth risk: it turns every chip back
        # on. Startups had this chip and Findings did not, so an identical row
        # of chips answered a click differently depending on the screen.
        self._all_risks_btn = QPushButton(tr("All"))
        self._all_risks_btn.setCheckable(True)
        self._all_risks_btn.setChecked(True)
        self._all_risks_btn.setObjectName("Subtle")
        self._all_risks_btn.setCursor(Qt.PointingHandCursor)
        self._all_risks_btn.clicked.connect(self._on_all_risks_clicked)
        filter_row.addWidget(self._all_risks_btn)

        self._risk_btns: dict[str, QPushButton] = {}
        for risk in RISK_ORDER:
            # tr(): the chip labels were the only risk names left in English.
            btn = QPushButton(tr(risk))
            btn.setCheckable(True)
            btn.setChecked(True)
            btn.setObjectName("Subtle")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(self._apply_risk_filter)
            filter_row.addWidget(btn)
            self._risk_btns[risk] = btn

        filter_row.addStretch()
        layout.addLayout(filter_row)
        self._refresh_risk_chip_styles()

        # Selection bar
        sel_bar = QHBoxLayout()
        sel_bar.setSpacing(10)

        self._sel_count_lbl = QLabel(tr("0 selected"))
        self._sel_count_lbl.setObjectName("Dim")
        self._sel_count_lbl.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 11px;")
        sel_bar.addWidget(self._sel_count_lbl)

        self._sel_size_lbl = QLabel("")
        self._sel_size_lbl.setObjectName("Dim")
        self._sel_size_lbl.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 11px;")
        sel_bar.addWidget(self._sel_size_lbl)

        sel_bar.addStretch()

        # Bulk select — shown only for disposable-data categories (see
        # _BULK_SELECT_CATEGORIES). Selects every item passing the current
        # filters, skipping anything Protected.
        self._btn_select_all = QPushButton(tr("Select all visible"))
        self._btn_select_all.setObjectName("Subtle")
        self._btn_select_all.setStyleSheet("font-size: 10px; padding: 2px 8px;")
        self._btn_select_all.setCursor(Qt.PointingHandCursor)
        self._btn_select_all.clicked.connect(self._select_all_visible)
        self._btn_select_all.setVisible(False)
        sel_bar.addWidget(self._btn_select_all)

        self._btn_clear_sel = QPushButton(tr("Clear"))
        self._btn_clear_sel.setObjectName("Subtle")
        self._btn_clear_sel.setStyleSheet("font-size: 10px; padding: 2px 8px;")
        self._btn_clear_sel.setCursor(Qt.PointingHandCursor)
        self._btn_clear_sel.clicked.connect(self._clear_selection_state)
        self._btn_clear_sel.setVisible(False)
        sel_bar.addWidget(self._btn_clear_sel)

        self._btn_clean = QPushButton(tr("Move to Recycle Bin"))
        self._btn_clean.setObjectName("Primary")
        self._btn_clean.setStyleSheet("font-size: 11px; padding: 4px 14px;")
        self._btn_clean.setCursor(Qt.PointingHandCursor)
        self._btn_clean.setEnabled(False)
        self._btn_clean.clicked.connect(self._show_cleanup)
        sel_bar.addWidget(self._btn_clean)

        layout.addLayout(sel_bar)

        # Model / proxy
        self._model = FindingsTableModel(self)
        self._proxy = FindingsFilterProxy(self)
        self._proxy.setSourceModel(self._model)
        self._proxy.set_sort_key("largest")
        self._proxy.setDynamicSortFilter(True)
        self._proxy.sort(COL_NAME, Qt.AscendingOrder)
        self._model.dataChanged.connect(self._on_model_data_changed)

        # Wire search to proxy via 200ms debounce (avoid O(n) filter on every keystroke)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(200)
        self._search_timer.timeout.connect(self._apply_search_now)
        self._search.textChanged.connect(self._on_search_text_changed)

        self._results_shell = QWidget()
        results_shell_layout = QHBoxLayout(self._results_shell)
        results_shell_layout.setContentsMargins(0, 0, 0, 0)
        results_shell_layout.setSpacing(12)

        self._results_stack = QWidget()
        self._results_stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._results_stack_layout = QVBoxLayout(self._results_stack)
        self._results_stack_layout.setContentsMargins(0, 0, 0, 0)
        self._results_stack_layout.setSpacing(0)
        results_shell_layout.addWidget(self._results_stack, stretch=7)

        self._list_panel = QFrame()
        self._list_panel.setObjectName("PanelAlt")
        self._list_panel.setMinimumHeight(320)
        self._list_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        list_outer = QVBoxLayout(self._list_panel)
        list_outer.setContentsMargins(0, 0, 0, 0)
        list_outer.setSpacing(0)

        list_hdr = QHBoxLayout()
        list_hdr.setContentsMargins(14, 12, 14, 10)
        list_hdr.setSpacing(8)
        self._list_title_lbl = QLabel(tr("FINDINGS LIST"))
        apply_tactical_label(self._list_title_lbl, font_size=9, letter_spacing=2)
        list_hdr.addWidget(self._list_title_lbl)
        self._list_count_lbl = QLabel(tr("// 0 visible"))
        self._list_count_lbl.setObjectName("Muted")
        self._list_count_lbl.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 9px;")
        list_hdr.addWidget(self._list_count_lbl)
        list_hdr.addStretch()
        list_outer.addLayout(list_hdr)

        self._list_sep = QFrame()
        self._list_sep.setFixedHeight(1)
        list_outer.addWidget(self._list_sep)

        self._list_scroll = QScrollArea()
        self._list_scroll.setWidgetResizable(True)
        self._list_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._list_scroll.setFrameShape(QFrame.NoFrame)
        self._list_scroll.setStyleSheet("border: none; background: transparent;")

        self._list_host = QWidget()
        self._list_layout = QVBoxLayout(self._list_host)
        self._list_layout.setContentsMargins(10, 8, 22, 8)
        self._list_layout.setSpacing(6)
        # Persistent empty-state label + trailing stretch. Pooled rows are
        # inserted between them, so neither is recreated on every filter pass.
        self._list_empty_lbl = QLabel(tr("No findings match the current search or status filters."))
        self._list_empty_lbl.setAlignment(Qt.AlignCenter)
        self._list_empty_lbl.setObjectName("Muted")
        self._list_empty_lbl.setStyleSheet("font-size: 13px; padding: 24px 0px;")
        self._list_empty_lbl.setVisible(False)
        self._list_layout.addWidget(self._list_empty_lbl)
        self._list_layout.addStretch()
        self._list_scroll.setWidget(self._list_host)
        list_outer.addWidget(self._list_scroll, stretch=1)
        self._results_stack_layout.addWidget(self._list_panel, stretch=1)

        self._right_sidebar = RightSidebar(
            open_cb=self._open_in_explorer,
            copy_cb=self._copy_path,
            recycle_cb=self._show_selected_cleanup,
            uninstall_cb=self._handle_deep_uninstall,
            ask_ai_cb=self._on_ask_ai,
            ask_ai_file_cb=self._on_ask_ai_file,
        )
        self._detail_widget = self._right_sidebar.detail_widget
        results_shell_layout.addWidget(self._right_sidebar, stretch=3)

        soft_line = QColor(get_palette().get("border", "#213028"))
        soft_line.setAlpha(58)
        line_rgba = (
            f"rgba({soft_line.red()}, {soft_line.green()}, "
            f"{soft_line.blue()}, {soft_line.alpha()})"
        )
        self._list_sep.setStyleSheet(f"background: {line_rgba}; border: none;")
        self._right_sidebar.apply_style()

        layout.addWidget(self._results_shell, stretch=1)

        # Cap notice — hidden unless the result was truncated
        self._cap_lbl = QLabel("")
        self._cap_lbl.setObjectName("Dim")
        self._cap_lbl.setStyleSheet(
            f"font-family: 'JetBrains Mono'; font-size: 10px; "
            f"color: {get_palette().get('review', '#d8b46a')};"
        )
        self._cap_lbl.setVisible(False)
        layout.addWidget(self._cap_lbl)

        # Footer — must be created before proxy signals are connected,
        # because setSourceModel fires modelReset immediately.
        footer = QHBoxLayout()
        self._footer_lbl = QLabel(tr("Showing 0 of 0 entities"))
        self._footer_lbl.setObjectName("Muted")
        self._footer_lbl.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 10px;")
        self._footer_lbl.setVisible(False)
        footer.addWidget(self._footer_lbl)
        footer.addStretch()
        layout.addLayout(footer)

        # Connect proxy reset/insert/remove signals only after _footer_lbl exists
        self._proxy.modelReset.connect(self._update_footer)
        self._proxy.rowsInserted.connect(self._update_footer)
        self._proxy.rowsRemoved.connect(self._update_footer)

    def _apply_title_color(self):
        """Re-tint the category title from the live palette.

        Separate from set_category so a theme switch can call it: the colour is
        baked into a stylesheet, and nothing re-applied it, so the drill-down
        heading kept the previous theme's hue until you navigated away.
        """
        color = _get_category_color(self.category)
        self._title_lbl.setStyleSheet(
            f"font-family: 'Silkscreen', 'JetBrains Mono'; font-size: 14px; "
            f"letter-spacing: 3px; color: {color};"
        )

    def set_category(self, category: str, entities: list, cap_notice: str = ""):
        """Set the category and entities to display."""
        self.category = category
        self.entities = entities
        self._selected_path = ""
        self._update_selection_display()
        self._clear_detail_sidebar()
        self._btn_select_all.setVisible(category in _BULK_SELECT_CATEGORIES)

        self._title_lbl.setText(category.upper())
        self._apply_title_color()

        total_size = sum(e.get("size_bytes", 0) for e in entities)
        self._stats_lbl.setText(tr("// {n} entities · {size}",
                                   n=len(entities), size=_format_size(total_size)))

        ai_analyzed = sum(1 for e in entities if e.get("ai_status") in ("ready", "done"))
        ai_pending = sum(1 for e in entities if e.get("ai_status") in ("pending", "analyzing"))
        ai_failed = sum(1 for e in entities if e.get("ai_status") in ("failed", "error"))

        if ai_pending:
            msg = f"AI analyzing · {ai_pending} queued/waiting"
            if ai_failed:
                msg += f" · {ai_failed} unavailable"
        elif ai_failed:
            msg = f"AI reviewed {ai_analyzed} item(s) · {ai_failed} unavailable"
        elif ai_analyzed:
            msg = f"AI reviewed {ai_analyzed} item(s)"
        else:
            msg = tr("AI queue idle")
        self._ai_summary_lbl.setText(msg)

        self._model.set_entities(entities)
        self._rebuild_entity_rows()
        if cap_notice:
            self._cap_lbl.setText(f"[!]  {cap_notice}")
            self._cap_lbl.setVisible(True)
        else:
            self._cap_lbl.setVisible(False)
        self._update_footer()

    def _on_model_data_changed(self, top_left, bottom_right, roles):
        if roles and Qt.CheckStateRole not in roles:
            return
        # Only refresh the checkbox visual of the rows that actually changed —
        # never re-render the whole list. This is what keeps toggling (and
        # bulk select-all) responsive.
        self._update_selection_display()
        if not self._row_widgets:
            return
        for src in range(top_left.row(), bottom_right.row() + 1):
            entity = self._model.get_entity(src)
            if not entity:
                continue
            row = self._row_widgets.get(entity.get("path", f"row:{src}"))
            if row is not None and row._source_row == src:
                row.set_checked(self._model.is_checked(src))

    def _on_search_text_changed(self, text: str):
        if not text:
            self._search_timer.stop()
            self._apply_search_now()
        else:
            self._search_timer.start()

    def _apply_search_now(self):
        self._proxy.set_search(self._search.text())
        self._rebuild_entity_rows()
        self._update_footer()

    def _on_all_risks_clicked(self):
        """'All' means no filter — turn every risk chip back on."""
        for btn in self._risk_btns.values():
            btn.setChecked(True)
        self._apply_risk_filter()

    def _apply_risk_filter(self):
        active = {r for r, btn in self._risk_btns.items() if btn.isChecked()}
        showing_everything = len(active) == len(self._risk_btns)
        self._proxy.set_risk_filter(None if showing_everything else active)
        self._all_risks_btn.setChecked(showing_everything)
        self._refresh_risk_chip_styles()
        self._rebuild_entity_rows()
        self._update_footer()

    def _select_all_visible(self):
        """Check every currently-visible (filtered) item that can actually be
        recycled — never personal/mixed containers or protected items."""
        rows: set[int] = set()
        for proxy_row in range(self._proxy.rowCount()):
            src = self._proxy.mapToSource(self._proxy.index(proxy_row, COL_NAME)).row()
            entity = self._model.get_entity(src)
            if not entity:
                continue
            if _entity_actionability(entity) == "protected":
                continue
            rows.add(src)
        if rows:
            self._model.set_checked_rows(rows, True)

    def _on_sort_changed(self, index: int):
        key = self._sort_combo.itemData(index)
        if not key:
            return
        self._proxy.set_sort_key(key)
        self._proxy.sort(COL_NAME, Qt.AscendingOrder)
        self._rebuild_entity_rows()

    def _update_footer(self, *args):
        visible = self._proxy.rowCount()
        total = self._model.rowCount()
        has_search = bool(self._search.text().strip())
        has_filter = any(not btn.isChecked() for btn in self._risk_btns.values())
        show_footer = has_search or has_filter or visible != total
        self._footer_lbl.setVisible(show_footer)
        if show_footer:
            self._footer_lbl.setText(tr("Showing {visible:,} of {total:,} entities",
                                        visible=visible, total=total))

    def _refresh_risk_chip_styles(self):
        """Uniform industrial chips — active shows the muted status accent,
        inactive recedes to a quiet faint label. No glow, no competing fills."""
        p = get_palette()
        panel  = p.get("panel_alt",  "#18241e")
        border = p.get("border_alt", "#2b3d33")
        quiet  = p.get("border",     "#213028")
        faint  = p.get("text_faint", "#57685e")

        def _chip_qss(active: bool, color: str) -> str:
            if active:
                return (f"font-size: 10px; padding: 5px 12px; color: {color}; "
                        f"background: {panel}; border: 1px solid {border}; "
                        f"border-radius: 2px;")
            return (f"font-size: 10px; padding: 5px 12px; color: {faint}; "
                    f"background: transparent; border: 1px solid {quiet}; "
                    f"border-radius: 2px;")

        for risk, btn in self._risk_btns.items():
            btn.setStyleSheet(_chip_qss(btn.isChecked(), _status_color(risk)))
        # "All" carries no risk colour of its own — it reads as active only
        # while nothing is filtered out.
        self._all_risks_btn.setStyleSheet(
            _chip_qss(self._all_risks_btn.isChecked(), p.get("text", "#d6e2da")))

    def changeEvent(self, event):
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.StyleChange:
            self._refresh_risk_chip_styles()
            self._sync_row_check_states()
            soft_line = QColor(get_palette().get("border", "#213028"))
            soft_line.setAlpha(58)
            line_rgba = (
                f"rgba({soft_line.red()}, {soft_line.green()}, "
                f"{soft_line.blue()}, {soft_line.alpha()})"
            )
            self._list_sep.setStyleSheet(f"background: {line_rgba}; border: none;")
            self._right_sidebar.apply_style()
        super().changeEvent(event)

    def _show_detail_sidebar(self, entity: dict):
        """Populate the persistent right-side inspector."""
        try:
            self._right_sidebar.populate(entity)
            QTimer.singleShot(0, self, self._ensure_selected_row_visible)
        except Exception as exc:
            import traceback
            print(f"[findings] detail sidebar error: {exc}\n{traceback.format_exc()}")
            self._right_sidebar.clear()

    def _ensure_selected_row_visible(self):
        if not self._selected_path:
            return
        row = self._row_widgets.get(self._selected_path)
        if row:
            self._list_scroll.ensureWidgetVisible(row, 0, 28)

    def _clear_detail_sidebar(self):
        self._right_sidebar.clear()

    def _open_in_explorer(self, path: str):
        """Open the entity path in file explorer."""
        import os
        import subprocess

        try:
            if not path:
                return
            target = os.path.abspath(os.path.normpath(path))
            if not os.path.exists(target):
                return
            if os.name == 'nt':
                if os.path.isdir(target):
                    subprocess.Popen(['explorer', target])
                else:
                    subprocess.Popen(['explorer', f'/select,{target}'])
            elif os.name == 'posix':
                if not os.path.isdir(target):
                    target = os.path.dirname(target)
                subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', target])
        except Exception:
            pass

    def _copy_path(self, path: str):
        """Copy entity path to clipboard."""
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(path)

    def set_scan_state(self, scan_state):
        self._scan_state = scan_state

    def _find_live_item(self, path: str):
        """Map a displayed entity/finding path back to its live object so the
        AI explainer can mutate the same instance the UI reads from.

        Uses ScanState's indexed lookup — a linear scan over a large findings
        list would stall the UI on every click.
        """
        if not path or not self._scan_state:
            return None
        return self._scan_state.find_by_path(path)

    def _on_ask_ai(self, entity: dict):
        """On-demand 'Ask AI' for a single item — works even when the bulk AI
        pass was never run. The explanation streams back via ai_finding_updated,
        which refreshes the inspector in place."""
        if not self._scan_state:
            return "unavailable"
        ai = getattr(self._scan_state, "ai_explainer", None)
        if not ai:
            return "unavailable"
        live = self._find_live_item(entity.get("path", ""))
        if live is None:
            return "unavailable"
        # Stamp the current session so the streamed result isn't discarded as
        # stale by ScanState._on_ai_finding_updated.
        ai._session_id = getattr(self._scan_state, "_session_id", "")
        reason = ai.explain_item(live)
        if reason == "no-model":
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(
                self,
                tr("AI model needed"),
                tr("Select an AI model in Settings to use Ask AI."),
            )
        return reason

    def _on_ask_ai_file(self, path: str):
        """On-demand AI for a single *file* inside an entity (Files tab).

        Opens a dialog that issues the request and fills in the answer when it
        arrives. Works with the bulk AI pass switched off.
        """
        if not self._scan_state:
            return
        ai = getattr(self._scan_state, "ai_explainer", None)
        if not ai:
            return
        live = self._find_live_item(path) or _finding_for_path(path)
        if live is None:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(
                self,
                tr("File not available"),
                tr("This file is no longer on disk, so there is nothing to "
                   "explain."),
            )
            return
        # Stamp the session so the streamed result isn't discarded as stale.
        ai._session_id = getattr(self._scan_state, "_session_id", "")
        from app.widgets.ask_ai_dialog import AskAIDialog
        AskAIDialog(live, ai, parent=self).exec()

    # ── Selection & cleanup ───────────────────────────────────────

    def _update_selection_display(self):
        checked = self._model.checked_entities()
        count = len(checked)
        if count == 0:
            self._sel_count_lbl.setText(tr("0 selected"))
            self._sel_size_lbl.setText("")
            self._btn_clear_sel.setVisible(False)
            self._btn_clean.setEnabled(False)
        else:
            total_size = self._model.checked_size()
            self._sel_count_lbl.setText(tr("{n} selected", n=count))
            self._sel_size_lbl.setText(f"· {_format_size(total_size)}")
            self._btn_clear_sel.setVisible(True)
            self._btn_clean.setEnabled(True)

    def _clear_selection_state(self):
        self._model.clear_checked()
        self._update_selection_display()

    def _show_cleanup(self):
        checked = self._model.checked_entities()
        if self._run_cleanup(checked):
            self._clear_selection_state()

    def _show_selected_cleanup(self, entity: dict):
        if self._run_cleanup([entity]):
            path = entity.get("path", "")
            if path and path == self._selected_path:
                self._selected_path = ""
                self._sync_row_selection()
                self._clear_detail_sidebar()

    def _run_cleanup(self, items: list[dict]) -> bool:
        if not items or not self._scan_state:
            return False
        session_id = getattr(self._scan_state, "_session_id", "")
        def _log(msg: str):
            if hasattr(self._scan_state, "log_line"):
                self._scan_state.log_line.emit(msg)
        # If the user turned off review confirmation ("Don't ask again"), the
        # dialog still opens for progress/result but auto-starts the move.
        store = getattr(self._scan_state, "_settings_store", None)
        confirm = store.get("confirm_risky_cleanup", True) if store else True
        dlg = CleanupConfirmDialog(
            items=items,
            scan_state=self._scan_state,
            session_id=session_id,
            log_fn=_log,
            auto_confirm=not confirm,
            parent=self,
        )
        dlg.exec()
        result = dlg.cleanup_result()
        if result and result.succeeded:
            # Remove the cleaned rows from THIS category view immediately so the
            # user can't re-click cleanup on items that are already gone.
            self._model.remove_cleaned(result.succeeded)
            self._rebuild_entity_rows()
            self._update_footer()
            self._clear_detail_sidebar()
            freed = _format_size(result.total_bytes_freed)
            n = len(result.succeeded)
            # The same sentence the cleanup dialog reports, and the locale
            # already carried it — this copy was an f-string, so the one
            # place a user reads the outcome after the dialog closes stayed
            # English in every language.
            self._show_toast(tr(
                "✓  {count} item(s) moved to Recycle Bin · {freed} freed",
                count=n, freed=freed))
            return True
        return False

    def _handle_deep_uninstall(self, entity: dict):
        name = entity.get("name") or entity.get("path") or "this application"
        uninstall_cmd = (entity.get("uninstall_string") or "").strip()

        if not uninstall_cmd:
            QMessageBox.information(
                self, tr("No uninstaller found"),
                tr("Windows has no registered uninstaller for {name}.\n\n"
                   "Use “Move to Recycle Bin” to remove leftover files you "
                   "recognize instead.").format(name=name),
            )
            return

        from app.services.uninstaller import (
            CANCELLED, LAUNCHED, launch_uninstaller, uninstaller_is_runnable,
        )

        # A registry entry outlives the program it describes. Say so plainly
        # rather than launching nothing and reporting success.
        if not uninstaller_is_runnable(uninstall_cmd):
            QMessageBox.information(
                self, tr("Uninstaller is missing"),
                tr("Windows still lists an uninstaller for {name}, but the file "
                   "it points to is gone — the entry is left over from a "
                   "program that was already removed or moved.\n\n"
                   "Use “Move to Recycle Bin” for any leftover files you "
                   "recognise.").format(name=name),
            )
            return

        reply = QMessageBox.question(
            self, tr("Deep Uninstall"),
            tr("Run the official uninstaller for {name}?\n\n"
               "This launches the application's own uninstaller. Windows will "
               "ask for permission first, because uninstalling needs "
               "administrator rights.").format(name=name),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        outcome, message = launch_uninstaller(uninstall_cmd)
        if self._scan_state and hasattr(self._scan_state, "log_line"):
            self._scan_state.log_line.emit(f"[uninstall] {name}: {message}")
        if outcome == CANCELLED:
            return          # the user declined UAC; nothing to report
        if outcome == LAUNCHED:
            # Both caches are now stale. The registry snapshot is obvious; the
            # presence evidence (Start Menu, installed folders, PATH, processes)
            # matters just as much — without clearing it, a re-scan still reports
            # the app as installed and tells the user to KEEP the very leftovers
            # they just uninstalled, which is the case this detection exists for.
            try:
                from app.services.entity_detector import invalidate_installed_programs_cache
                from app.services.app_presence import reset_cache as reset_presence_cache
                invalidate_installed_programs_cache()
                reset_presence_cache()
            except Exception:
                pass
            self._show_toast(tr(
                "Uninstaller launched · {name} — re-scan to confirm removal",
                name=name))
        else:
            QMessageBox.warning(self, tr("Deep Uninstall failed"), message)

    def _show_toast(self, message: str, ms: int = 5000):
        self._sel_size_lbl.setText(message)
        self._sel_size_lbl.setStyleSheet(
            "font-family: 'JetBrains Mono'; font-size: 11px; "
            f"color: {get_palette().get('safe', '#7aa88a')};"
        )
        QTimer.singleShot(ms, self, self._clear_toast)

    def _clear_toast(self):
        self._sel_size_lbl.setText("")
        self._sel_size_lbl.setStyleSheet(
            "font-family: 'JetBrains Mono'; font-size: 11px;"
        )

    def clear_selection(self):
        """Clear selection and reset the inspector. Called when navigating back."""
        self._model.clear_checked()
        self._selected_path = ""
        for row in self._row_widgets.values():
            row.set_selected(False)
        self._clear_detail_sidebar()

    def select_by_path(self, path: str) -> bool:
        """Select the row for *path*, expanding its group if it is folded away.

        Used when the by-folder view hands an entity back: the user found it
        by location, so the list has to land on it rather than merely open in
        the vicinity.
        """
        for row in range(self._model.rowCount()):
            entity = self._model.get_entity(row)
            if not entity or entity.get("path", "") != path:
                continue
            owner = owner_key(path, self._app_index())
            if owner:
                self._expanded_groups.add(owner.lower())
            self._selected_path = path
            self._rebuild_entity_rows()
            self._show_detail_sidebar(entity)
            return True
        return False

    # ── grouping ──────────────────────────────────────────────────

    def _display_rows(self):
        """Yield ``(source_row, entity, depth, group)`` in visual order.

        One app is one row. A real scan put 23 Discord rows in this list —
        `shared_proto_db`, `Session Storage`, `WidevineCdm` — none of which is
        a decision anyone can make, and ~120 more for AppData/Local/Packages.
        Members are folded under the app that owns them.

        Nothing is dropped: a collapsed group still reports its own totals and
        its members are one click away. Small items in particular stay
        reachable, because clearing them out is a thing people actually want
        to do — the group just lets them do it in one go.
        """
        ordered = []
        for proxy_row in range(self._proxy.rowCount()):
            sr = self._proxy.mapToSource(
                self._proxy.index(proxy_row, COL_NAME)).row()
            entity = self._model.get_entity(sr)
            if entity:
                ordered.append((sr, entity))

        groups = group_entities([e for _sr, e in ordered], self._app_index())
        row_of = {id(e): sr for sr, e in ordered}

        # A group's row shows the whole app's total, so it has to be RANKED by
        # that total. Left in first-appearance order it was positioned by its
        # largest single member instead, and a 618 MB Discord could sit below
        # a 300 MB row while claiming to be bigger. Only the size-like sorts
        # have a meaningful aggregate; the rest keep the proxy's order.
        sort_key = self._proxy.sort_key()
        if sort_key in ("largest", "smallest", "reclaimable"):
            field = ("reclaimable_bytes" if sort_key == "reclaimable"
                     else "size_bytes")
            groups.sort(key=lambda g: g.get(field, 0),
                        reverse=(sort_key != "smallest"))

        for group in groups:
            members = group["members"]
            root = group["root"]
            total = len(members) + (1 if root else 0)
            if total <= 1:
                only = root or (members[0] if members else None)
                if only is not None:
                    yield row_of.get(id(only), -1), only, 0, None
                continue

            key = (group.get("owner") or "").lower()
            expanded = key in self._expanded_groups
            header = {
                "count": total, "expanded": expanded, "key": key,
                "members": members, "root": root,
            }
            yield (row_of.get(id(root), -1) if root else -1,
                   self._group_header_entity(group, total),
                   0, header)
            if expanded:
                if root is not None:
                    yield row_of.get(id(root), -1), root, 1, None
                for member in members:
                    yield row_of.get(id(member), -1), member, 1, None

    def _app_index(self):
        """Registry install-location → display name, read once per screen."""
        if getattr(self, "_app_index_cache", None) is None:
            from app.models.entity_grouping import build_app_index
            self._app_index_cache = build_app_index()
        return self._app_index_cache

    def _group_header_entity(self, group: dict, total: int) -> dict:
        """A display-only dict describing the group as a whole."""
        root = group.get("root") or {}
        return {
            "name": group_label(group),
            "path": group.get("owner", ""),
            "size": _format_size(group.get("size_bytes", 0)),
            "size_bytes": group.get("size_bytes", 0),
            "reclaimable_bytes": sum(
                int(e.get("reclaimable_bytes", 0) or 0)
                for e in ([root] if root else []) + group["members"]),
            "file_count": group.get("file_count", 0),
            "risk": _group_risk(group),
            "entity_type": root.get("entity_type", "application"),
            "entity_type_label": tr("{n} items in this app", n=total),
            "is_group": True,
            "ai_status": "none",
        }

    def _group_fully_checked(self, group: dict | None) -> bool:
        if not group:
            return False
        rows = self._group_source_rows(group)
        return bool(rows) and all(self._model.is_checked(r) for r in rows)

    def _group_source_rows(self, group: dict) -> list[int]:
        entities = list(group.get("members") or [])
        if group.get("root") is not None:
            entities.append(group["root"])
        rows = []
        for e in entities:
            r = self._model.row_for_entity(e)
            if r >= 0:
                rows.append(r)
        return rows

    def _toggle_group(self, key: str):
        if key in self._expanded_groups:
            self._expanded_groups.discard(key)
        else:
            self._expanded_groups.add(key)
        self._rebuild_entity_rows()

    def _rebuild_entity_rows(self):
        """Repopulate the list by reusing pooled row widgets.

        Filtering/sorting/searching only rebinds existing widgets to the new
        set of entities (and hides any surplus) instead of destroying and
        recreating the whole list, which is what made filter clicks janky.
        """
        visible = self._proxy.rowCount()
        self._row_widgets.clear()

        # Count the ROWS on screen, not the entities behind them. Grouping
        # folds 9 findings into 3 rows, and a header reading "9 visible" above
        # 3 rows just looks wrong.
        rows = list(self._display_rows())
        shown = len(rows)
        self._list_count_lbl.setText(
            tr("// {n:,} visible", n=shown) if shown == visible
            else tr("// {shown:,} rows · {total:,} items", shown=shown, total=visible))

        if visible == 0:
            for row in self._row_pool:
                row.setVisible(False)
            self._list_empty_lbl.setVisible(True)
            self._selected_path = ""
            self._clear_detail_sidebar()
            self._update_footer()
            return

        self._list_empty_lbl.setVisible(False)

        selected_visible = False
        idx = 0
        for sr, entity, depth, group in rows:
            checked = (self._model.is_checked(sr) if sr >= 0
                       else self._group_fully_checked(group))
            if idx < len(self._row_pool):
                row = self._row_pool[idx]
                row.rebind(sr, entity, checked, depth=depth, group=group)
                row.setVisible(True)
            else:
                row = FindingsEntityRow(sr, entity, checked)
                row.clicked.connect(self._select_source_row)
                row.check_toggled.connect(self._set_checked_state)
                row.group_toggled.connect(self._toggle_group)
                self._row_pool.append(row)
                # Insert just before the trailing stretch so order stays stable.
                self._list_layout.insertWidget(self._list_layout.count() - 1, row)
                row.rebind(sr, entity, checked, depth=depth, group=group)
            path = entity.get("path", "")
            is_selected = (group is None and bool(path)
                           and path == self._selected_path)
            row.set_selected(is_selected)
            selected_visible = selected_visible or is_selected
            if group is None:
                self._row_widgets[path or f"row:{sr}"] = row
            idx += 1

        # Park any unused pooled rows.
        for j in range(idx, len(self._row_pool)):
            self._row_pool[j].setVisible(False)

        if not selected_visible:
            self._selected_path = ""
            self._clear_detail_sidebar()
        self._update_footer()

    def _sync_row_selection(self):
        for path, row in self._row_widgets.items():
            row.set_selected(path == self._selected_path and bool(self._selected_path))

    def _sync_row_check_states(self):
        # Called on theme change: restyle each visible row (set_selected →
        # _apply_style refreshes frame + label colors) and re-tint its badge,
        # which Badge only does when explicitly asked.
        for path, row in self._row_widgets.items():
            row.set_checked(self._model.is_checked(row._source_row))
            row.set_selected(path == self._selected_path and bool(self._selected_path))
            row._risk_badge.refresh_style()

    def update_entity(self, entity: dict):
        """Update a single entity in-place without resetting filters/checks."""
        row_idx = self._model.update_entity_by_path(entity)
        if row_idx < 0:
            return
        path = entity.get("path", "")
        row = self._row_widgets.get(path)
        if row:
            checked = self._model.data(self._model.index(row_idx, COL_CHECK), Qt.CheckStateRole) == Qt.Checked
            row.update_entity(entity, checked)
            row.set_selected(path == self._selected_path and bool(self._selected_path))
        if path and path == self._selected_path:
            self._show_detail_sidebar(entity)

    def _select_source_row(self, source_row: int):
        entity = self._model.get_entity(source_row)
        if not entity:
            return
        path = entity.get("path", "")
        if path and path == self._selected_path:
            self._selected_path = ""
            self._sync_row_selection()
            self._clear_detail_sidebar()
            return
        self._selected_path = path
        self._sync_row_selection()
        self._show_detail_sidebar(entity)

    def _set_checked_state(self, source_row: int, checked: bool):
        # A group header owns no row of its own (source_row -1 when the app
        # folder produced no entity), so ticking it has to fan out to every
        # member. This is the point of grouping for small items: clearing 363
        # scattered fragments is one click, not 363.
        sender = self.sender()
        if isinstance(sender, FindingsEntityRow) and sender.is_group_header():
            for row in self._group_source_rows(sender._group):
                self._apply_check(row, checked)
            self._rebuild_entity_rows()
            return
        self._apply_check(source_row, checked)

    def _apply_check(self, source_row: int, checked: bool):
        if source_row < 0:
            return
        self._model.setData(
            self._model.index(source_row, COL_CHECK),
            Qt.Checked if checked else Qt.Unchecked,
            Qt.CheckStateRole,
        )


class EmptyStateWidget(QFrame):
    """Empty state shown when no scan has been performed."""

    def __init__(self, parent=None, on_start_scan: Callable = None):
        super().__init__(parent)
        self.on_start_scan = on_start_scan

        self.setObjectName("Panel")
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 60, 40, 60)
        layout.setSpacing(20)
        layout.setAlignment(Qt.AlignCenter)

        p = get_palette()

        # Icon/illustration placeholder
        icon_lbl = QLabel("◈")
        icon_lbl.setStyleSheet(f"font-size: 64px; color: {p.get('accent_soft', '#1b2e22')};")
        icon_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(icon_lbl)

        # Title
        title = QLabel(tr("ANALYSIS NOT STARTED"))
        title.setStyleSheet(
            f"font-family: 'Silkscreen', 'JetBrains Mono'; font-size: 14px; letter-spacing: 4px; "
            f"color: {p.get('text_dim', '#8a9b8f')};"
        )
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        # Description
        desc = QLabel(
            tr("Run an analysis to see a visual breakdown of your storage.\n"
               "Vigil will categorize files into meaningful groups.")
        )
        desc.setStyleSheet(
            f"font-size: 13px; color: {p.get('text_faint', '#57685e')}; line-height: 1.5;"
        )
        desc.setAlignment(Qt.AlignCenter)
        desc.setWordWrap(True)
        layout.addWidget(desc)

        layout.addSpacing(20)

        # Start button
        if self.on_start_scan:
            start_btn = QPushButton(tr("Start Analysis →"))
            start_btn.setObjectName("Primary")
            start_btn.setStyleSheet("padding: 10px 24px; font-size: 13px;")
            start_btn.setCursor(Qt.PointingHandCursor)
            start_btn.clicked.connect(self.on_start_scan)
            layout.addWidget(start_btn, alignment=Qt.AlignCenter)

        layout.addStretch()


class LoadingStateWidget(QFrame):
    """Loading state shown while entity detection is in progress."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._in_style_change = False
        self.setObjectName("Panel")
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 60, 40, 60)
        layout.setSpacing(16)
        layout.setAlignment(Qt.AlignCenter)

        p = get_palette()

        # Spinner. U+25D4, not U+25D0 (see _AI_SYMBOL): the half-filled
        # circle is in neither bundled font and drew as a .notdef box.
        self._spinner_lbl = QLabel("\u25d4")
        self._spinner_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._spinner_lbl)

        # Title
        self._title_lbl2 = QLabel(tr("PREPARING STORAGE OVERVIEW"))
        self._title_lbl2.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._title_lbl2)

        # Current stage label
        self._phase_lbl = QLabel(tr("Initializing..."))
        self._phase_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._phase_lbl)

        layout.addSpacing(8)

        # Semantic coverage stats grid
        self._stats_frame = QFrame()
        stats_layout = QGridLayout(self._stats_frame)
        stats_layout.setContentsMargins(24, 16, 24, 16)
        stats_layout.setHorizontalSpacing(32)
        stats_layout.setVerticalSpacing(10)

        self._stat_labels = []
        self._stat_vals = []

        def _add_stat(row, col, label_text, attr_name):
            lbl = QLabel(label_text)
            val = QLabel("\u2014")
            stats_layout.addWidget(lbl, row * 2,     col)
            stats_layout.addWidget(val, row * 2 + 1, col)
            setattr(self, attr_name, val)
            self._stat_labels.append(lbl)
            self._stat_vals.append(val)

        _add_stat(0, 0, tr("GROUPED FILES"),    "_stat_grouped")
        _add_stat(0, 1, tr("UNKNOWN FILES"),    "_stat_unknown")
        _add_stat(1, 0, tr("ENTITIES CREATED"), "_stat_entities")
        _add_stat(1, 1, tr("COVERAGE"),         "_stat_coverage")

        layout.addWidget(self._stats_frame)
        layout.addSpacing(4)

        # Reassuring note
        self._note_lbl = QLabel(tr("Unknown files are normal \u2014 they will be grouped into Mixed Storage."))
        self._note_lbl.setAlignment(Qt.AlignCenter)
        self._note_lbl.setWordWrap(True)
        layout.addWidget(self._note_lbl)

        layout.addStretch()

        self._apply_loading_colors(p)

    def _apply_loading_colors(self, p: dict = None):
        if p is None:
            p = get_palette()
        accent   = p.get("accent",      "#7cc596")
        text_dim = p.get("text_dim",    "#8a9b8f")
        text     = p.get("text",        "#d6e2da")
        faint    = p.get("text_faint",  "#57685e")
        panel    = p.get("panel",       "#141d18")
        border   = p.get("border",      "#213028")

        self._spinner_lbl.setStyleSheet(f"font-size: 48px; color: {accent};")
        self._title_lbl2.setStyleSheet(
            f"font-family: 'Silkscreen', 'JetBrains Mono'; font-size: 14px; letter-spacing: 3px; color: {text_dim};"
        )
        self._phase_lbl.setStyleSheet(
            f"font-family: 'JetBrains Mono'; font-size: 11px; color: {accent};"
        )
        self._stats_frame.setStyleSheet(
            f"background: {panel}; border: 1px solid {border}; border-radius: 2px;"
        )
        for lbl in self._stat_labels:
            lbl.setStyleSheet(f"font-family: 'JetBrains Mono'; font-size: 10px; color: {faint}; border: none;")
        for val in self._stat_vals:
            val.setStyleSheet(f"font-family: 'JetBrains Mono'; font-size: 13px; color: {text}; font-weight: bold; border: none;")
        self._note_lbl.setStyleSheet(f"font-size: 11px; color: {faint};")

    def changeEvent(self, event):
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.StyleChange:
            self._apply_loading_colors()
        super().changeEvent(event)

    def set_phase(self, phase: str, message: str = ""):
        """Update the current stage label."""
        phase_display = {
            "filesystem":        tr("Scanning filesystem\u2026"),
            "entity_detection":  tr("Grouping into semantic categories\u2026"),
            "ai_classification": tr("AI analyzing entities\u2026"),
            "error":             message or tr("An error occurred"),
        }.get(phase, message or tr("Processing\u2026"))
        self._phase_lbl.setText(phase_display)
        p = get_palette()
        if phase == "error":
            self._phase_lbl.setStyleSheet(
                f"font-family: 'JetBrains Mono'; font-size: 11px; color: {p.get('risk', '#d68a78')};"
            )
        else:
            self._phase_lbl.setStyleSheet(
                f"font-family: 'JetBrains Mono'; font-size: 11px; color: {p.get('accent', '#7cc596')};"
            )

    def set_entity_progress(self, grouped_files: int, ungrouped_files: int, entities_created: int, coverage_pct: int = 0):
        """Update the semantic coverage stats panel."""
        self._stat_grouped.setText(f"{grouped_files:,}")
        self._stat_unknown.setText(f"{ungrouped_files:,}")
        self._stat_entities.setText(f"{entities_created:,}")
        self._stat_coverage.setText(f"{coverage_pct}%")


class StoppedStateWidget(QFrame):
    """Stopped state shown when analysis was stopped before completion."""

    def __init__(self, parent=None, on_resume: Callable = None):
        super().__init__(parent)
        self.on_resume = on_resume
        self.setObjectName("Panel")
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 60, 40, 60)
        layout.setSpacing(20)
        layout.setAlignment(Qt.AlignCenter)

        # Icon
        icon_lbl = QLabel("◪")
        _p = get_palette()
        icon_lbl.setStyleSheet(f"font-size: 48px; color: {_p.get('review', '#d8b46a')};")
        icon_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(icon_lbl)

        # Title
        title = QLabel(tr("ANALYSIS STOPPED"))
        title.setStyleSheet(
            f"font-family: 'Silkscreen', 'JetBrains Mono'; font-size: 14px; letter-spacing: 3px; "
            f"color: {_p.get('text_dim', '#8a9b8f')};"
        )
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        # Description
        desc = QLabel(
            tr("The analysis was stopped before the storage map was fully ready.\n"
               "Partial results have been preserved.")
        )
        desc.setStyleSheet(
            f"font-size: 13px; color: {_p.get('text_faint', '#57685e')}; line-height: 1.5;"
        )
        desc.setAlignment(Qt.AlignCenter)
        desc.setWordWrap(True)
        layout.addWidget(desc)

        layout.addSpacing(20)

        # Resume button
        if self.on_resume:
            resume_btn = QPushButton(tr("Resume Analysis →"))
            resume_btn.setObjectName("Primary")
            resume_btn.setStyleSheet("padding: 10px 24px; font-size: 13px;")
            resume_btn.setCursor(Qt.PointingHandCursor)
            resume_btn.clicked.connect(self.on_resume)
            layout.addWidget(resume_btn, alignment=Qt.AlignCenter)

        layout.addStretch()


class FindingsDashboard(QWidget):
    """Main findings dashboard with semantic storage map.
    
    Three distinct states:
    1. No scan: "Analysis has not been started yet"
    2. In progress: "Storage map is being prepared" (loading state)
    3. Complete: Storage Map with category blocks
    
    Signals:
        navigate_to_analyze: Emitted when user wants to start a new analysis
            (connected in main.py to switch to Analyze screen)
    """
    
    # Signal emitted when "Start Analysis" button clicked in empty state
    navigate_to_analyze = Signal()

    def __init__(self, scan_state=None, parent=None):
        super().__init__(parent)
        self._scan_state = scan_state
        self._viewed_categories: set[str] = set()

        # View state machine
        self._current_view = "dashboard"  # dashboard | category | entity
        self._current_category: Optional[str] = None
        
        # Performance: debounced refresh
        self._refresh_timer: Optional[QTimer] = None
        self._pending_refresh = False
        self._last_category_data: Optional[dict] = None

        self._build_ui()
        self._connect_signals()

        # Findings has a lot of inline-palette styling (StorageOverview cards,
        # the entity detail block, the donut chart, category cards). Re-apply
        # them when the theme changes so colours don't stay stale.
        from app.themes.theme_manager import theme_signaller
        theme_signaller().theme_changed.connect(self._on_theme_changed)

        # Initial state check
        self._update_for_current_state()

    def _on_theme_changed(self, _key: str = ""):
        try:
            if hasattr(self, "_overview_view") and self._overview_view is not None:
                self._overview_view._apply_panel_colors()
                if hasattr(self._overview_view, "_donut"):
                    self._overview_view._donut.update()
            if hasattr(self, "_category_view") and self._category_view is not None:
                cv = self._category_view
                # The category heading bakes its hue into a stylesheet too.
                if getattr(cv, "category", ""):
                    cv._apply_title_color()
                if hasattr(cv, "_detail_widget") and cv._detail_widget is not None:
                    cv._detail_widget._apply_block_styles()
                    # Built with the palette that was live at construction, so
                    # without this the Ask AI button kept the old theme's
                    # accent and border after a switch.
                    btn = getattr(cv._detail_widget, "_ai_ask_btn", None)
                    if btn is not None:
                        btn.setStyleSheet(ask_ai_button_qss())
                # Same for the sort dropdown: apply_reference_style() bakes the
                # palette in, and nothing re-applied it.
                combo = getattr(cv, "_sort_combo", None)
                if combo is not None:
                    combo.apply_reference_style(get_palette(), compact=True)
        except Exception:
            pass

    def _build_ui(self):
        self._main_layout = QVBoxLayout(self)
        self._main_layout.setContentsMargins(0, 0, 0, 0)
        self._main_layout.setSpacing(0)

        page_header = QHBoxLayout()
        page_header.setContentsMargins(22, 16, 22, 12)
        page_header.setSpacing(10)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        self._page_title = QLabel(tr("FINDINGS"))
        apply_tactical_label(self._page_title, font_size=16, letter_spacing=4)
        title_col.addWidget(self._page_title)

        self._page_sub = QLabel(tr("semantic storage map"))
        self._page_sub.setObjectName("Dim")
        self._page_sub.setStyleSheet("font-size: 12px;")
        title_col.addWidget(self._page_sub)
        page_header.addLayout(title_col)
        page_header.addStretch()
        self._main_layout.addLayout(page_header)

        # Stacked widget for view switching
        self._stack = QStackedWidget()

        # View 1: Empty state (no scan ever run)
        self._empty_view = EmptyStateWidget(on_start_scan=self._on_start_scan)
        self._stack.addWidget(self._empty_view)

        # View 2: Loading state (scan in progress, entity detection running)
        self._loading_view = LoadingStateWidget()
        self._stack.addWidget(self._loading_view)

        # View 3: Stopped state (analysis stopped before completion)
        self._stopped_view = StoppedStateWidget(on_resume=self._on_resume_analysis)
        self._stack.addWidget(self._stopped_view)

        # View 4: Dashboard — inner stack: Overview (default) + Map
        self._dashboard_container = self._build_dashboard_container()
        self._stack.addWidget(self._dashboard_container)

        # View 5: Category detail
        self._category_view = CategoryDetailView(on_back=self._on_back_to_dashboard)
        self._stack.addWidget(self._category_view)

        # View 6: By folder — the escape hatch when a classification is wrong.
        self._tree_view = FolderTreeView(on_back=self._on_back_to_dashboard)
        self._tree_view.entity_activated.connect(self._show_detail_for_entity)
        self._stack.addWidget(self._tree_view)

        self._main_layout.addWidget(self._stack)

        # Show appropriate state initially
        self._update_for_current_state()

    def _build_dashboard_container(self) -> QWidget:
        """Build the dashboard — StorageOverviewWidget is the sole visualization."""
        self._overview_view = StorageOverviewWidget(
            on_category_click=self._on_category_click
        )
        self._overview_view.browse_by_folder.connect(self._show_tree)
        # Let the dashboard fill the available width. The category list inside
        # already has stretch=1 and the summary panel is capped at 340px wide,
        # so the table grows into the empty space and the summary stays right.
        self._overview_view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        host = QWidget()
        lay = QHBoxLayout(host)
        lay.setContentsMargins(22, 0, 22, 18)
        lay.addWidget(self._overview_view, stretch=1)
        return host

    @property
    def _dashboard_view(self):
        """Compatibility shim — returns the StorageOverviewWidget."""
        return self._overview_view

    def _connect_signals(self):
        """Connect to ScanState signals. Called on init and when scan_state changes."""
        if not self._scan_state:
            return
        ss = self._scan_state
        ss.scan_started.connect(self._on_scan_started)
        ss.scan_finished.connect(self._on_scan_finished)
        ss.entities_ready.connect(self._on_entities_ready)
        ss.skipped_entries_updated.connect(self._on_skipped_updated)
        ss.ui_refresh.connect(self._on_ui_refresh)
        ss.ai_finding_updated.connect(self._on_ai_finding_updated)
        ss.scan_phase_changed.connect(self._on_phase_changed)
        ss.scan_halted.connect(self._on_scan_halted)
        ss.entity_progress.connect(self._on_entity_progress)


    def _update_for_current_state(self):
        """Determine and show the correct view based on scan state.
        
        States:
        1. No scan run: Show empty state
        2. Filesystem scan or entity detection in progress: Show loading state
        3. Stopped: Show stopped state  
        4. Entities ready (even if AI running): Show dashboard immediately
        5. Complete: Show dashboard
        
        KEY: Findings shows dashboard as soon as entities are ready.
        AI may continue in background - don't wait for it.
        """
        if not self._scan_state:
            self._show_empty()
            return
        
        phase = self._scan_state.current_phase
        has_entities = self._scan_state.has_entities
        is_active = self._scan_state.is_analysis_active

        # Priority: stopped > loading > dashboard > empty
        if phase == "stopped":
            self._show_stopped()
        # Show loading ONLY during filesystem scan or entity detection
        # Once entities are ready, switch to dashboard even if AI is running
        elif phase in ("filesystem", "entity_detection") or (is_active and not has_entities):
            self._show_loading(phase)
        elif has_entities or phase in ("ai_classification", "complete"):
            self._show_dashboard()
        elif self._scan_state.display_count() > 0:
            # Findings exist but no entities (e.g. restored session) — show dashboard
            self._show_dashboard()
        else:
            self._show_empty()

    def _show_empty(self):
        self._stack.setCurrentWidget(self._empty_view)
        self._current_view = "empty"

    def _show_loading(self, phase: str = ""):
        """Show loading state with current phase."""
        if phase:
            self._loading_view.set_phase(phase)
        self._stack.setCurrentWidget(self._loading_view)
        self._current_view = "loading"

    def _show_stopped(self):
        """Show stopped state."""
        self._stack.setCurrentWidget(self._stopped_view)
        self._current_view = "stopped"

    def _on_resume_analysis(self):
        """Navigate to analyze screen to resume analysis."""
        self.navigate_to_analyze.emit()

    def _on_skipped_updated(self):
        """Handle new skipped/protected entries from ScanState."""
        if not self._scan_state:
            return
        entries = self._scan_state.skipped_entries
        # Update loading view count label if visible
        if self._current_view == "loading":
            self._loading_view.set_phase(
                "entity_detection",
                f"Grouping storage\u2026 ({len(entries):,} protected/skipped)",
            )
        # Always update the dashboard block so it's ready when entities_ready fires
        self._dashboard_view.update_skipped(entries)

    def _on_phase_changed(self, phase: str, message: str):
        """Handle scan phase change."""
        if self._current_view == "loading":
            self._loading_view.set_phase(phase, message)

    def _on_entity_progress(self, phase: str, grouped_files: int, ungrouped_files: int, entities_created: int, coverage_pct: int = 0):
        """Handle entity detection progress updates."""
        if self._current_view == "loading":
            _phase_labels = {
                "started":          "Detecting applications\u2026",
                "known_dirs":       "Detecting known directories\u2026",
                "applications":     "Detecting applications\u2026",
                "browser_profiles": "Grouping browser profiles\u2026",
                "cache_folders":    "Grouping cache and temp folders\u2026",
                "protected_paths":  "Grouping system files\u2026",
                "content_grouping": "Grouping media folders\u2026",
                "grouping":         "Grouping remaining folders\u2026",
                "unknown_sweep":    "Building fallback groups\u2026",
                "complete":         "Grouping complete",
            }
            self._loading_view.set_phase(
                "entity_detection",
                _phase_labels.get(phase, "Grouping storage\u2026"),
            )
            self._loading_view.set_entity_progress(grouped_files, ungrouped_files, entities_created, coverage_pct)

    def _on_entities_ready(self):
        """Handle entities ready — switch from loading to dashboard."""
        try:
            entity_count = self._scan_state.entity_count if self._scan_state else 0
            if entity_count == 0:
                # Restore mode: show findings-based dashboard even without entities
                has_findings = self._scan_state and self._scan_state.display_count() > 0
                if has_findings:
                    self._last_category_data = None
                    self._refresh_dashboard()
                    if self._current_view != "category":
                        self._show_dashboard()
                else:
                    self._loading_view.set_phase("error", "No semantic entities were created")
                return
            self._last_category_data = None
            self._refresh_dashboard()
            if self._current_view != "category":
                self._show_dashboard()
        except Exception as e:
            self._loading_view.set_phase("error", f"Storage map could not be rendered: {e}")

    def _on_scan_halted(self):
        """Handle scan halted - show stopped state."""
        self._update_for_current_state()

    def _show_dashboard(self):
        self._refresh_dashboard()
        self._stack.setCurrentWidget(self._dashboard_container)
        self._current_view = "dashboard"

    def _show_tree(self):
        """Open the by-folder view over everything the scan found."""
        items = []
        if self._scan_state is not None:
            items = (self._scan_state.display_items()
                     if hasattr(self._scan_state, "display_items") else [])
        self._tree_view.set_entities(items)
        self._stack.setCurrentWidget(self._tree_view)
        self._current_view = "tree"

    def _show_detail_for_entity(self, entity: dict):
        """A folder picked in the tree opens in its category's list, selected —
        so the tree hands the user back to the view that can act on it."""
        category = entity.get("category") or "Unknown"
        self._show_category(category)
        path = entity.get("path", "")
        if path:
            self._category_view.select_by_path(path)

    def _show_category(self, category: str):
        entities, total = self._get_entities_for_category(category)
        cap = self._ENTITY_CAP
        cap_notice = f"Showing {cap:,} of {total:,} — use Analyze for the full list." if total > cap else ""
        self._category_view.set_category(category, entities, cap_notice=cap_notice)
        self._stack.setCurrentWidget(self._category_view)
        self._current_view = "category"
        self._current_category = category
        self._viewed_categories.add(category)

    def open_category(self, category_name: str) -> bool:
        """Open Findings directly to a specific category (called from Analyze category click)."""
        normalized = canonical_category(category_name)

        if not self._scan_state:
            return False

        category_data = self._aggregate_by_category()
        matched_category = next(
            (cat for cat in category_data if cat.lower() == normalized.lower()), None
        )

        if matched_category is None:
            self._show_dashboard()
            return False

        self._current_category = None
        self._show_category(matched_category)
        return True

    def _on_start_scan(self):
        """Navigate to analyze screen to start scan."""
        self.navigate_to_analyze.emit()

    def _on_category_click(self, category: str):
        """Handle category block click."""
        self._show_category(category)

    def _on_back_to_dashboard(self):
        """Return to dashboard from category view."""
        # Clear the category view state before going back
        self._category_view.clear_selection()
        self._show_dashboard()

    def _on_scan_started(self, target: str):
        """Scan started - show loading state (entities not ready yet)."""
        self._show_loading("filesystem")

    def _on_scan_finished(self):
        """Scan finished - show loading state for entity detection."""
        # Don't show dashboard yet - entity detection is still running
        # The entities_ready signal will trigger _on_entities_ready to show dashboard
        if self._current_view not in ("category", "dashboard"):
            self._show_loading("entity_detection")

    def _on_ui_refresh(self):
        """Throttled refresh from ScanState — debounced to prevent freezes."""
        # Debounce: schedule refresh instead of doing it immediately
        if self._refresh_timer is None:
            self._refresh_timer = QTimer(self)
            self._refresh_timer.setSingleShot(True)
            self._refresh_timer.timeout.connect(self._do_refresh)
        
        self._pending_refresh = True
        self._refresh_timer.start(REFRESH_DEBOUNCE_MS)

    def _on_ai_finding_updated(self, item):
        """Refresh visible selected/category AI state without resetting the view."""
        if self._current_view != "category" or not self._current_category:
            return
        try:
            entity = item.to_dict() if hasattr(item, "to_dict") else dict(item)
        except Exception:
            return
        cat_norm = canonical_category(entity.get("category"))
        if cat_norm != self._current_category:
            return
        self._category_view.update_entity(entity)
        
    def _do_refresh(self):
        """Perform the actual refresh (called after debounce delay)."""
        if not self._pending_refresh:
            return
        self._pending_refresh = False
        
        if self._current_view == "dashboard":
            self._refresh_dashboard()
        # Category view intentionally NOT refreshed: it is a stable snapshot of
        # entities at open time. Resetting the model mid-view causes crashes when
        # the proxy rebuilds its sort mapping while AI/dedup workers are active.

    def _refresh_dashboard(self):
        """Refresh the storage map with current data."""
        if not self._scan_state:
            return

        category_data = self._aggregate_by_category()
        data_hash = f"{sum(d['size_bytes'] for d in category_data.values())}:{len(category_data)}"

        if self._last_category_data == data_hash:
            return
        self._last_category_data = data_hash

        self._dashboard_view.update_categories(category_data)

        for cat in self._viewed_categories:
            self._dashboard_view.mark_viewed(cat)

    def _aggregate_by_category(self) -> dict:
        """Aggregate scan state data by category."""
        if not self._scan_state:
            return {}

        # When semantic entities are available, iterate the entity dict cache (small set).
        # When only raw findings are available (e.g. restored session with no entity detection),
        # use the pre-aggregated category_summary() counters — iterating findings_as_dicts()
        # on a 2M-file scan would OOM.
        if self._scan_state.has_entities:
            items = self._scan_state.display_items()
        elif hasattr(self._scan_state, "category_summary"):
            summary = self._scan_state.category_summary()
            categories: dict[str, dict] = {}
            for cat, data in summary.items():
                cat_norm = canonical_category(cat)
                if cat_norm not in categories:
                    categories[cat_norm] = {
                        "size_bytes": 0,
                        "reclaimable_bytes": 0,
                        "count": 0,
                        "ai_analyzed": 0,
                        "ai_pending": 0,
                        "ai_failed": 0,
                    }
                categories[cat_norm]["size_bytes"] += data.get("size_bytes", 0)
                categories[cat_norm]["count"] += data.get("count", 0)
            return categories
        else:
            return {}

        categories = {}
        for item in items:
            cat = canonical_category(item.get("category"))
            if cat not in categories:
                categories[cat] = {
                    "size_bytes": 0,
                    "reclaimable_bytes": 0,
                    "count": 0,
                    "ai_analyzed": 0,
                    "ai_pending": 0,
                    "ai_failed": 0,
                }
            categories[cat]["size_bytes"] += item.get("size_bytes", 0)
            categories[cat]["reclaimable_bytes"] += item.get("reclaimable_bytes", 0)
            categories[cat]["count"] += 1
            ai_status = item.get("ai_status", "none")
            if ai_status in ("ready", "done"):
                categories[cat]["ai_analyzed"] += 1
            elif ai_status in ("pending", "analyzing"):
                categories[cat]["ai_pending"] += 1
            elif ai_status in ("failed", "error"):
                categories[cat]["ai_failed"] += 1

        return categories

    # Maximum entities to load into the table — prevents OOM on scans with millions of files.
    _ENTITY_CAP = 5_000

    def _get_entities_for_category(self, category: str) -> tuple[list, int]:
        """Return (capped_entities, total_count) for the given category."""
        if not self._scan_state:
            return [], 0

        if hasattr(self._scan_state, "findings_for_category"):
            return self._scan_state.findings_for_category(category, limit=self._ENTITY_CAP)

        # Fallback: iterate pre-built dict list (safe only when not a 2M+ scan)
        all_items = self._scan_state.display_items() if hasattr(self._scan_state, "display_items") else []
        cat_norm = canonical_category(category)
        matched = []
        for e in all_items:
            if canonical_category(e.get("category")) == cat_norm:
                matched.append(e)
                if len(matched) >= self._ENTITY_CAP * 2:
                    break
        return matched[:self._ENTITY_CAP], len(matched)

    def set_scan_state(self, scan_state):
        """Connect to shared scan state."""
        self._scan_state = scan_state
        self._category_view.set_scan_state(scan_state)
        self._connect_signals()
        if scan_state and scan_state.has_entities:
            self._last_category_data = None
            self._update_for_current_state()
