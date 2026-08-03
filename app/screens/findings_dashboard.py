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
    QLineEdit, QTextEdit, QFrame, QTableView, QMessageBox,
    QHeaderView, QAbstractItemView, QScrollArea, QGridLayout,
    QSizePolicy, QSpacerItem, QStackedWidget, QTabWidget
)
from PySide6.QtCore import Qt, QTimer, QSize, Signal, QObject
from PySide6.QtGui import QColor, QFont, QPainter, QFontMetrics, QPen

from app.widgets.pills import Badge
from app.models.finding import _format_size
from app.themes.theme_manager import get_category_colors, get_palette
from app.screens.cleanup_dialog import CleanupConfirmDialog
from app.models.findings_table_model import (
    FindingsTableModel, FindingsFilterProxy, FindingsDelegate, COL_CHECK, COL_NAME,
)
from app.models.risk import normalize_risk, risk_fg as _risk_fg, risk_variant as _risk_variant
from app.models.smart_entity import actionability_for_type
from app.i18n import tr
from app.widgets.panels import apply_tactical_label
from app.widgets.controls import TacticalComboBox


# ── Performance Constants ───────────────────────────────────────────

def _ask_ai_button_qss() -> str:
    """Accent-tinted style so an 'Ask AI' button reads clearly as an action,
    not as a run of plain text. Themed via the live palette, with a filled
    hover state."""
    p = get_palette()
    accent = p.get("accent", "#7cc596")
    soft = p.get("accent_soft", "#1b2e22")
    bg = p.get("panel", "#141d18")
    faint = p.get("text_faint", "#57685e")
    border = p.get("border", "#213028")
    return (
        f"QPushButton {{ background: {soft}; color: {accent}; "
        f"border: 1px solid {accent}; border-radius: 3px; "
        f"padding: 3px 12px; font-size: 11px; font-weight: 600; }}"
        f"QPushButton:hover {{ background: {accent}; color: {bg}; }}"
        f"QPushButton:pressed {{ background: {accent}; color: {bg}; }}"
        f"QPushButton:disabled {{ background: transparent; color: {faint}; "
        f"border-color: {border}; }}"
    )


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


def _elide_path_middle(path: str, max_len: int = 54) -> str:
    """Shorten a path for a single-line display, keeping the drive and the tail.

    Long paths otherwise wrapped and stretched the inspection panel. Middle
    elision preserves the two ends a user reads — the drive/root and the file
    itself — e.g. ``C:/Users/…/CachedData/c97a3f/index.db``. Display only; the
    full path is kept as the widget tooltip and used verbatim by Copy/Open.
    """
    p = str(path or "").replace("\\", "/")
    if len(p) <= max_len:
        return p
    parts = [seg for seg in p.split("/") if seg]
    if len(parts) <= 2:
        return p[: max_len - 1] + "…"
    head = parts[0]                       # drive / root
    tail_parts = parts[1:]
    tail = tail_parts[-1]
    # Grow the tail from the end until we hit the budget.
    for i in range(len(tail_parts) - 2, 0, -1):
        candidate = "/".join(tail_parts[i:])
        if len(head) + len(candidate) + 5 > max_len:
            break
        tail = candidate
    return f"{head}/…/{tail}"


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
            return f"Newest copy {locations[0]['modified']}"
        return "Duplicate activity unknown"
    install_date = entity.get("install_date", "")
    if install_date:
        return f"Installed {install_date}"
    accessed = entity.get("last_access", "")
    if accessed and accessed != "—":
        return f"Last active {accessed}"
    modified = entity.get("first_seen", "")
    if modified and modified != "—":
        return f"Updated {modified}"
    age = entity.get("age", "")
    if age and age != "—":
        return f"Age {age}"
    return "Recent activity unknown"


def _entity_importance_text(entity: dict) -> str:
    if entity.get("entity_type") == "duplicate_group":
        if entity.get("risk") == "Protected":
            return "High — duplicate touches protected system locations"
        if entity.get("risk") == "Review":
            return "High — verify app/project ownership before removing copies"
        return "Medium — remove only clear extra copies"
    risk = entity.get("risk", "Review")
    if entity.get("cloud_sync_provider"):
        return "High — synced with cloud storage"
    if risk == "Protected":
        return "High — protected by system rules"
    if risk == "Review":
        return "High — may contain personal or app data"
    if risk == "Optional":
        return "Medium — likely removable if no longer needed"
    return "Low — generally safe to regenerate"


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
        parts.append(f"{file_count:,} files")
    if folder_count:
        parts.append(f"{folder_count:,} folders")
    label = entity.get("entity_type_label") or entity.get("semantic_label") or ""
    if label:
        parts.append(label)
    # Say so when the row stands for a list rather than a folder. Without this
    # "Loose archives in Downloads" looks like one indivisible thing, and the
    # per-file view — which is what a group row is for — goes unnoticed.
    if _entity_file_group_size(entity) >= 2:
        parts.append(tr("choose individual files"))
    return " · ".join(parts) if parts else "Contents not summarized"


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


def _has_uninstaller(entity: dict) -> bool:
    """True when the app exposes a real registry uninstaller command."""
    return bool((entity.get("uninstall_string") or "").strip())


def launch_uninstaller(uninstall_string: str) -> tuple[bool, str]:
    """Run an app's native uninstaller command. Returns (started, message)."""
    cmd = (uninstall_string or "").strip()
    if not cmd:
        return False, "no uninstaller command available"
    try:
        import subprocess
        # The registry string is a full command line (often quoted with args),
        # so let the shell parse it exactly as Windows would.
        subprocess.Popen(cmd, shell=True)
        return True, "uninstaller launched"
    except Exception as exc:  # pragma: no cover - platform/runtime dependent
        return False, f"could not launch uninstaller: {exc}"


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
            tr("Recommendation: this is Vigil — the app doing the cleaning. You can "
               "remove it whenever you like, but it would be good to let it finish "
               "the job first. 🙂"),
            tr("Vigil's own files: the app, your settings, and the scan history "
               "this screen is showing. Vigil will not clean itself up."),
            accent_info,
        )
    if risk == "Protected":
        return (
            tr("PROTECTED"),
            tr("Recommendation: keep this item. It is marked protected and should not be cleaned from Findings."),
            entity.get("risk_reason") or tr("Protected rule matched this path or entity type."),
            accent_risk,
        )
    if is_duplicate and _duplicate_is_per_app_binary(entity):
        return (
            tr("KEEP COPIES"),
            tr("Recommendation: keep every copy — each belongs to a separate program that needs its own. To free space, uninstall an app you no longer use instead of deleting this file."),
            entity.get("risk_reason") or _duplicate_subtitle(entity),
            accent_review,
        )
    if not is_duplicate and not is_app and _is_content_container(entity):
        return (
            tr("REVIEW INSIDE"),
            tr("Recommendation: Vigil won't delete this whole folder — it holds personal or mixed content. Open it to review, or reclaim space from specific items inside (duplicates, very old large files)."),
            entity.get("risk_reason") or tr("Personal or mixed content — deleting everything here is rarely what you want."),
            accent_review,
        )
    if is_app:
        return (
            tr("SYSTEM-LEVEL"),
            tr("Recommendation: use Deep Uninstall for applications; recycle only leftover files you recognize."),
            entity.get("risk_reason") or tr("Application metadata or installer/package signals were detected."),
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
            entity.get("recommendation") or entity.get("risk_reason") or tr("This is likely removable but may still be useful."),
            accent_info,
        )
    return (
        tr("NEEDS REVIEW"),
        tr("Recommendation: inspect the path, owner, and AI reasoning before cleanup."),
        entity.get("risk_reason") or tr("Vigil does not have enough confidence to mark this as safe."),
        accent_review,
    )

# Sort options for detail view
SORT_OPTIONS = [
    ("largest", "Largest first"),
    ("smallest", "Smallest first"),
    ("ai_analyzed", "AI analyzed"),
    ("risk", "Status"),
    ("safe_cleanup", "Safe cleanup"),
    ("last_access", "Last accessed"),
    ("reclaimable", "Reclaimable size"),
]


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
        self._total_bytes: int = 0
        self._scan_label: str = ""
        self._selected: str = ""
        self._hovered: str = ""
        self.setMinimumSize(220, 220)
        self.setMaximumSize(300, 300)
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)

    def set_data(self, sorted_cats: list[tuple], total_bytes: int, scan_label: str = ""):
        """sorted_cats: list of (category, data_dict) sorted by size desc."""
        from PySide6.QtGui import QColor as _QColor
        self._total_bytes = total_bytes
        self._scan_label = scan_label
        self._segments.clear()

        # Build angle map — each segment proportional to real size
        total = sum(d["size_bytes"] for _, d in sorted_cats) or 1
        angle = 0.0
        for cat, data in sorted_cats:
            span = 360.0 * data["size_bytes"] / total
            if span < 0.5:
                span = 0.5
            color = _get_category_color(cat)
            self._segments.append({
                "cat": cat,
                "pct": data.get("percentage", 0),
                "size_bytes": data["size_bytes"],
                "color": color,
                "angle_start": angle,
                "angle_span": span,
            })
            angle += span
        self.update()

    def set_selected(self, category: str):
        self._selected = category
        self.update()

    def paintEvent(self, event):
        from PySide6.QtGui import QPainter, QColor, QPen, QFont, QFontMetrics
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

        GAP_DEG = 1.4   # softer angular separation between sectors

        for seg in self._segments:
            a_start = seg["angle_start"]
            a_span  = seg["angle_span"]

            is_sel = seg["cat"] == self._selected
            is_hov = seg["cat"] == self._hovered

            color = QColor(seg["color"])
            if is_sel:
                color = color.lighter(138)
            elif is_hov:
                color = color.lighter(118)

            painter.setBrush(color)
            # Hairline separator between sectors, drawn in the panel bg.
            painter.setPen(QPen(QColor(get_palette().get("bg_deep", "#0a100c")), 1))

            # Qt: angles are 1/16th degree, start=top (90°), CCW positive
            # We map: 0° = top, CW, so Qt angle = (90 - a_start) * 16
            qt_start = int((90.0 - a_start - a_span / 2 * 0 + 0) * 16)
            qt_start = int((90.0 - a_start) * 16)
            qt_span  = int(-(a_span - GAP_DEG) * 16)

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
        hole_color = QColor(get_palette().get("bg_deep", "#0b140d"))
        painter.setBrush(hole_color)
        painter.setPen(QPen(hole_color, 1))
        painter.drawEllipse(hole_rect)

        # ── Center text ─────────────────────────────────────────────
        # Priority: hovering > selected > default
        active = self._hovered or self._selected
        if active and self._segments:
            seg = next((s for s in self._segments if s["cat"] == active), None)
            if seg:
                self._draw_center_text(painter, cx, cy,
                    f"{seg['pct']:.1f}%",
                    seg["cat"],
                    _format_size(seg["size_bytes"]),
                )
                return

        # Default: total size + category count (no percentage)
        self._draw_center_text(painter, cx, cy,
            _format_size(self._total_bytes),
            f"{len(self._segments)} categories",
            self._scan_label,
        )

    def _draw_center_text(self, painter, cx, cy, line1: str, line2: str, line3: str):
        from PySide6.QtGui import QColor, QFont
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
        if cat:
            self._selected = cat
            self.update()
            self.sector_clicked.emit(cat)


# ── Category card for the overview list ──────────────────────────

class CategoryCardWidget(QFrame):
    """Single row in the overview category list."""

    clicked  = Signal(str)
    hovered  = Signal(str)   # emits category name or "" on leave

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
        row.setContentsMargins(10, 0, 14, 0)
        row.setSpacing(8)

        # Color swatch
        swatch = QFrame()
        swatch.setFixedSize(4, 34)
        swatch.setStyleSheet(f"background: {self._color}; border: none; border-radius: 2px;")
        row.addWidget(swatch)

        # Category name
        name_wrap = QWidget()
        name_wrap.setStyleSheet("background: transparent; border: none;")
        name_row = QHBoxLayout(name_wrap)
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(0)
        self._name_lbl = QLabel(tr(category).upper())
        self._name_lbl.setFixedWidth(142)
        name_row.addWidget(self._name_lbl)
        row.addWidget(name_wrap)

        row.addStretch()

        p = get_palette()

        # Item count
        self._count_lbl = QLabel(f"{data.get('count', 0):,} items")
        self._count_lbl.setFixedWidth(80)
        self._count_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self._count_lbl)

        # Size
        self._size_lbl = QLabel(_format_size(data.get("size_bytes", 0)))
        self._size_lbl.setFixedWidth(72)
        self._size_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self._size_lbl)

        # Percentage label
        pct = data.get("percentage", 0)
        self._pct_lbl = QLabel(f"{pct:.1f}%")
        self._pct_lbl.setFixedWidth(52)
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
        tint = p.get("tint_bg", "#0f1914")
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

        list_hdr = QHBoxLayout()
        list_hdr.setContentsMargins(10, 10, 14, 8)
        self._hdr_labels = []
        for txt, w in [(tr("CATEGORY"), 160), (tr("ITEMS"), 80), (tr("SIZE"), 72), ("%", 52)]:
            h = QLabel(txt)
            h.setFixedWidth(w)
            h.setAlignment(Qt.AlignRight | Qt.AlignVCenter if txt != "CATEGORY" else Qt.AlignLeft | Qt.AlignVCenter)
            list_hdr.addWidget(h)
            self._hdr_labels.append(h)
        list_hdr.insertSpacing(0, 14)   # swatch gap
        list_hdr.addStretch()
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
            k = QLabel(label)
            row.addWidget(k)
            row.addStretch()
            v = QLabel("—")
            v.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 12px;")
            v.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
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
        # Remove old cards
        for card in list(self._cards.values()):
            self._cards_layout.removeWidget(card)
            card.deleteLater()
        self._cards.clear()

        # Remove old stretch
        while self._cards_layout.count():
            item = self._cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

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

        from PySide6.QtWidgets import QGridLayout, QTextEdit, QScrollArea

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
            l = QLabel(text)
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
        self._path_val  = _mk_val()
        # The path is elided to one line (full path in the tooltip) so a long
        # path can't wrap and stretch the inspection panel.
        self._path_val.setWordWrap(False)
        self._size_key  = _mk_key("SIZE:")
        self._size_val  = _mk_val()
        self._items_key = _mk_key("CONTAINS:")
        self._items_val = _mk_val()
        self._activity_key = _mk_key("LAST ACTIVE:")
        self._activity_val = _mk_val()
        self._importance_key = _mk_key("IMPORTANCE:")
        self._importance_val = _mk_val()

        def _meta_row(key_widget: QLabel, value_widget: QWidget) -> QWidget:
            row = QWidget()
            row_l = QHBoxLayout(row)
            row_l.setContentsMargins(0, 0, 0, 0)
            row_l.setSpacing(10)
            key_widget.setFixedWidth(88 if self._compact else 112)
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

        self._dup_text = QTextEdit()
        self._dup_text.setReadOnly(True)
        self._dup_text.setMaximumHeight(126)
        self._dup_text.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._dup_text.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        dup_l.addWidget(self._dup_text)
        self._dup_section.setVisible(False)
        root.addWidget(self._dup_section)

        # ── Contextual reasoning block (full width, below) ────────────
        self._ai_title = QLabel(tr("CONTEXTUAL REASONING"))
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
        self._ai_state_badge = QLabel()
        self._ai_state_badge.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 11px;")
        ai_hdr_row.addWidget(self._ai_state_badge)
        ai_hdr_row.addStretch()
        # On-demand "Ask AI" — explain just this item even when the bulk AI pass
        # wasn't run. Hidden unless the item has no answer yet (set in update()).
        self._ai_ask_btn = QPushButton(tr("Ask AI"))
        self._ai_ask_btn.setCursor(Qt.PointingHandCursor)
        self._ai_ask_btn.setStyleSheet(_ask_ai_button_qss())
        self._ai_ask_btn.setVisible(False)
        self._ai_ask_btn.clicked.connect(self._on_ask_ai_clicked)
        ai_hdr_row.addWidget(self._ai_ask_btn)
        ai_frame_layout.addLayout(ai_hdr_row)

        self._ai_scroll = QScrollArea()
        self._ai_scroll.setWidgetResizable(True)
        self._ai_scroll.setFrameShape(QScrollArea.NoFrame)
        self._ai_scroll.setMaximumHeight(156)
        self._ai_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        ai_container = QWidget()
        ai_layout = QVBoxLayout(ai_container)
        ai_layout.setContentsMargins(0, 0, 0, 0)
        ai_layout.setSpacing(4)
        self._ai_content_lbl = QLabel()
        self._ai_content_lbl.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 12px;")
        self._ai_content_lbl.setWordWrap(True)
        self._ai_content_lbl.setVisible(False)
        ai_layout.addWidget(self._ai_content_lbl)

        self._ai_text = QTextEdit()
        self._ai_text.setReadOnly(True)
        self._ai_text.setStyleSheet(
            "QTextEdit { background: transparent; border: none; "
            "font-family: 'JetBrains Mono'; font-size: 12px; }"
        )
        self._ai_text.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._ai_text.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._ai_text.setMaximumHeight(132)
        self._ai_text.setVisible(False)
        ai_layout.addWidget(self._ai_text)

        self._ai_scroll.setWidget(ai_container)
        self._ai_scroll.setVisible(False)
        ai_frame_layout.addWidget(self._ai_scroll)

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
        self._tabs.addTab(self._build_files_page(), tr("Files"))
        # Hidden (not just disabled) until an entity with browsable files is
        # selected — a greyed-out empty tab reads as "broken" to users.
        self._tabs.setTabEnabled(1, False)
        self._tabs.setTabVisible(1, False)

        self._apply_block_styles()

    # ── Files tab (paginated per-file browser) ────────────────────────

    _FILES_PER_PAGE = 50

    def _build_files_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)

        # Top row: live selection counter + per-page select / clear.
        top = QHBoxLayout()
        top.setSpacing(8)
        self._files_count_lbl = QLabel(tr("0 selected"))
        self._files_count_lbl.setStyleSheet(
            "font-family: 'JetBrains Mono'; font-size: 12px; font-weight: bold;"
        )
        top.addWidget(self._files_count_lbl)
        top.addStretch()
        self._btn_files_select_page = QPushButton(tr("Select page"))
        self._btn_files_select_page.setObjectName("Subtle")
        self._btn_files_select_page.setStyleSheet("font-size: 10px; padding: 2px 8px;")
        self._btn_files_select_page.setCursor(Qt.PointingHandCursor)
        self._btn_files_select_page.clicked.connect(self._files_select_page)
        top.addWidget(self._btn_files_select_page)
        self._btn_files_clear = QPushButton(tr("Clear"))
        self._btn_files_clear.setObjectName("Subtle")
        self._btn_files_clear.setStyleSheet("font-size: 10px; padding: 2px 8px;")
        self._btn_files_clear.setCursor(Qt.PointingHandCursor)
        self._btn_files_clear.clicked.connect(self._files_clear_selection)
        top.addWidget(self._btn_files_clear)
        lay.addLayout(top)

        # File rows.
        self._files_scroll = QScrollArea()
        self._files_scroll.setWidgetResizable(True)
        self._files_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._files_scroll.setStyleSheet("QScrollArea { border: none; }")
        self._files_container = QWidget()
        self._files_clay = QVBoxLayout(self._files_container)
        self._files_clay.setContentsMargins(0, 0, 0, 0)
        self._files_clay.setSpacing(2)
        self._files_clay.addStretch()
        self._files_scroll.setWidget(self._files_container)
        lay.addWidget(self._files_scroll, stretch=1)

        # Pagination row.
        pager = QHBoxLayout()
        pager.setSpacing(8)
        self._btn_files_prev = QPushButton(tr("‹ Prev"))
        self._btn_files_prev.setObjectName("Subtle")
        self._btn_files_prev.setStyleSheet("font-size: 10px; padding: 2px 10px;")
        self._btn_files_prev.setCursor(Qt.PointingHandCursor)
        self._btn_files_prev.clicked.connect(lambda: self._files_change_page(-1))
        pager.addWidget(self._btn_files_prev)
        self._files_page_lbl = QLabel("")
        self._files_page_lbl.setObjectName("Dim")
        self._files_page_lbl.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 11px;")
        self._files_page_lbl.setAlignment(Qt.AlignCenter)
        pager.addWidget(self._files_page_lbl, stretch=1)
        self._btn_files_next = QPushButton(tr("Next ›"))
        self._btn_files_next.setObjectName("Subtle")
        self._btn_files_next.setStyleSheet("font-size: 10px; padding: 2px 10px;")
        self._btn_files_next.setCursor(Qt.PointingHandCursor)
        self._btn_files_next.clicked.connect(lambda: self._files_change_page(1))
        pager.addWidget(self._btn_files_next)
        lay.addLayout(pager)

        # Action.
        self._btn_recycle_files = QPushButton(tr("Recycle selected files"))
        self._btn_recycle_files.setObjectName("Primary")
        self._btn_recycle_files.setCursor(Qt.PointingHandCursor)
        self._btn_recycle_files.setEnabled(False)
        self._btn_recycle_files.clicked.connect(self._on_recycle_files)
        lay.addWidget(self._btn_recycle_files)

        # State.
        self._all_file_paths: list = []
        self._selected_files: set = set()
        self._files_page_idx = 0
        self._file_checks: list = []   # (QCheckBox, path) for the CURRENT page
        return page

    # ── Slot helpers ──────────────────────────────────────────────────

    def _apply_ai_reasoning_visibility(self):
        self._ai_scroll.setVisible(True)
        self._ai_text.setVisible(self._ai_has_long_reasoning)

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
        self._ai_content_lbl.setVisible(True)
        self._ai_text.setVisible(False)
        self._apply_ai_reasoning_visibility()

    # ── Contained-files list ──────────────────────────────────────────

    def _collect_entity_files(self, entity: dict) -> list:
        """Concrete files this entity stands for, for the expandable list.

        Prefers the stored ``removable_file_paths`` (loose buckets, installers);
        falls back to a live, capped folder listing for content-collection
        folders so photo/video/document groups can also be inspected.
        """
        rfp = [p for p in (entity.get("removable_file_paths") or []) if p]
        if rfp:
            return rfp
        if not _is_content_container(entity):
            return []
        path = entity.get("path", "")
        if not path or not os.path.isdir(path):
            return []
        out: list = []
        try:
            for de in os.scandir(path):
                try:
                    if de.is_file(follow_symlinks=False):
                        out.append(de.path)
                except OSError:
                    pass
                if len(out) >= 500:
                    break
        except OSError:
            pass
        return out

    def _populate_files_section(self, entity: dict):
        """Load the entity's files into the paginated Files tab (or disable it)."""
        is_app = _is_application_action_target(entity)
        paths = [] if is_app else self._collect_entity_files(entity)

        # A single-file or contentless entity adds no insight — keep Files off.
        if len(paths) < 2:
            self._all_file_paths = []
            self._selected_files = set()
            self._files_page_idx = 0
            self._file_checks = []
            self._tabs.setTabEnabled(1, False)
            self._tabs.setTabVisible(1, False)
            self._tabs.setTabText(1, tr("Files"))
            if self._tabs.currentIndex() == 1:
                self._tabs.setCurrentIndex(0)
            return

        self._all_file_paths = paths
        self._selected_files = set()
        self._files_page_idx = 0
        self._tabs.setTabEnabled(1, True)
        self._tabs.setTabVisible(1, True)
        self._tabs.setTabText(1, tr("Files ({n})").format(n=len(paths)))
        self._render_files_page()

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

    def _file_page_count(self) -> int:
        return max(1, (len(self._all_file_paths) + self._FILES_PER_PAGE - 1)
                   // self._FILES_PER_PAGE)

    def _render_files_page(self):
        """Draw the current page of file rows; selection persists across pages."""
        while self._files_clay.count() > 1:
            item = self._files_clay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._file_checks = []

        pages = self._file_page_count()
        self._files_page_idx = max(0, min(self._files_page_idx, pages - 1))
        start = self._files_page_idx * self._FILES_PER_PAGE
        page_paths = self._all_file_paths[start:start + self._FILES_PER_PAGE]

        faint = get_palette().get("text_faint", "#57685e")
        for p in page_paths:
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(8)
            cb = QCheckBox(os.path.basename(p) or p)
            cb.setToolTip(p)
            cb.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 11px;")
            cb.setChecked(p in self._selected_files)
            cb.toggled.connect(lambda checked, path=p: self._on_file_toggle(path, checked))
            rl.addWidget(cb, stretch=1)
            size_lbl = QLabel(self._file_size_str(p))
            size_lbl.setStyleSheet(
                f"font-family: 'JetBrains Mono'; font-size: 10px; color: {faint};"
            )
            size_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            rl.addWidget(size_lbl)
            # Per-file on-demand AI: explain just this one file, without
            # needing the bulk AI pass to have run.
            if self._ask_ai_file_cb is not None:
                ask = QPushButton(tr("Ask AI"))
                ask.setStyleSheet(_ask_ai_button_qss())
                ask.setCursor(Qt.PointingHandCursor)
                ask.setToolTip(tr("Explain this file with AI"))
                ask.clicked.connect(
                    lambda _checked=False, path=p: self._ask_ai_file_cb(path)
                )
                rl.addWidget(ask)
            self._files_clay.insertWidget(self._files_clay.count() - 1, row)
            self._file_checks.append((cb, p))

        self._files_page_lbl.setText(
            tr("Page {i} of {n}").format(i=self._files_page_idx + 1, n=pages))
        self._btn_files_prev.setEnabled(self._files_page_idx > 0)
        self._btn_files_next.setEnabled(self._files_page_idx < pages - 1)
        self._update_files_counter()

    @staticmethod
    def _file_size_str(path: str) -> str:
        try:
            return _format_size(os.path.getsize(path))
        except OSError:
            return "—"

    def _on_file_toggle(self, path: str, checked: bool):
        if checked:
            self._selected_files.add(path)
        else:
            self._selected_files.discard(path)
        self._update_files_counter()

    def _files_select_page(self):
        for cb, p in self._file_checks:
            if not cb.isChecked():
                cb.setChecked(True)   # toggled → adds to _selected_files
        self._update_files_counter()

    def _files_clear_selection(self):
        self._selected_files = set()
        for cb, _p in self._file_checks:
            cb.blockSignals(True)
            cb.setChecked(False)
            cb.blockSignals(False)
        self._update_files_counter()

    def _files_change_page(self, delta: int):
        self._files_page_idx += delta
        self._render_files_page()

    def _update_files_counter(self):
        n = len(self._selected_files)
        total = len(self._all_file_paths)
        self._files_count_lbl.setText(tr("{n} of {total} selected").format(n=n, total=total))
        self._btn_recycle_files.setEnabled(n > 0 and self._recycle_cb is not None)
        self._btn_recycle_files.setText(
            tr("Recycle {n} selected file(s)").format(n=n) if n
            else tr("Recycle selected files")
        )

    def _on_recycle_files(self):
        selected = sorted(self._selected_files)
        if not selected or not self._recycle_cb:
            return
        item = dict(self._current_entity)
        item["removable_file_paths"] = selected
        item["entity_type"] = self._current_entity.get("entity_type", "")
        item["name"] = tr("{n} file(s) from {group}").format(
            n=len(selected), group=self._current_entity.get("name", "group"))
        self._recycle_cb(item)

    # ── Theming ───────────────────────────────────────────────────────

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

        self._risk_badge.set_badge(risk.upper(), _status_variant(risk))

        _pal = get_palette()
        _ai_safe  = _pal.get("safe",       "#7aa88a")
        _ai_warn  = _pal.get("review",     "#c7a66c")
        _ai_risk  = _pal.get("risk",       "#c67a69")
        _ai_idle  = _pal.get("text_faint", "#57685e")
        _ai_map = {
            "ready": ("✓ AI", _ai_safe), "done":      ("✓ AI", _ai_safe),
            "pending": ("◐ AI", _ai_warn), "analyzing": ("◐ AI", _ai_warn),
            "failed": ("✗ AI", _ai_risk), "error":     ("✗ AI", _ai_risk),
            "none": ("— AI", _ai_idle), "disabled": ("⊘ AI", _ai_idle),
        }
        ai_txt, ai_col = _ai_map.get(ai_status, ("—", _ai_idle))
        self._ai_badge.setText(ai_txt)
        self._ai_badge.setStyleSheet(
            f"font-family: 'JetBrains Mono'; font-size: 11px; color: {ai_col};"
        )

        # Info rows
        self._cat_val.setText(category)
        if is_duplicate:
            self._path_val.setText(_duplicate_path_preview(entity))
            self._path_val.setToolTip("")
        else:
            self._path_val.setText(_elide_path_middle(path) if path else "—")
            self._path_val.setToolTip(path or "")
        self._size_val.setText(size)
        self._items_val.setText(contains_text or f"{item_count:,} items")
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
        self._lbl_val.setText(semantic_label)
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
                self._ai_content_lbl.setVisible(True)
                self._ai_text.setVisible(False)
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
                self._ai_content_lbl.setVisible(False)
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
                self._ai_content_lbl.setVisible(True)
                self._ai_text.setVisible(False)
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
                self._ai_content_lbl.setVisible(True)
                self._ai_text.setVisible(False)
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
                self._ai_content_lbl.setVisible(True)
                self._ai_text.setVisible(False)
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
                self._ai_content_lbl.setVisible(True)
                self._ai_text.setVisible(False)
                self._apply_ai_reasoning_visibility()
            else:
                self._ai_state_badge.setText("")
                self._ai_scroll.setVisible(False)
        else:
            self._ai_state_badge.setText("")
            self._ai_scroll.setVisible(False)

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
        # of Program Files folders (gstreamer, Fortinet, vendor components…)
        # register no uninstall command, and an enabled button that can only
        # ever answer "no uninstaller found" is a dead end. Keep it visible but
        # disabled with the reason, and recycle stays available as the fallback.
        can_uninstall = (is_app and has_uninstaller
                         and self._uninstall_cb is not None)
        self._btn_uninstall.setVisible(is_app)
        self._btn_uninstall.setEnabled(can_uninstall)
        self._btn_uninstall.setToolTip(
            "" if can_uninstall else
            tr("Windows has no registered uninstaller for this application — "
               "remove leftover files with Move to Recycle Bin instead.")
        )
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


class _FindingSelectionCheckBox(QCheckBox):
    """Same square checkbox renderer used by Quick Cleanup rows."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(18, 18)

    def paintEvent(self, event):
        del event
        p = get_palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)

        checked = self.isChecked()
        enabled = self.isEnabled()
        hovered = self.underMouse()

        border = p.get("border_alt", "#2b3d33")
        border_hover = p.get("border_hover", "#3a5648")
        border_active = p.get("accent", "#7cc596")
        fill = p.get("bg_deep", "#080d0a")
        fill_hover = p.get("panel_hover", "#1d2c25")
        fill_active = p.get("accent_soft", "#1b2e22")
        tick = p.get("text", "#d6e2da")
        disabled = p.get("text_faint", "#57685e")

        box = self.rect().adjusted(1, 1, -1, -1)
        current_border = border_active if checked else border_hover if hovered else border
        current_fill = fill_active if checked else fill_hover if hovered else fill
        if not enabled:
            current_border = border
            current_fill = fill

        painter.setPen(QPen(QColor(current_border), 1))
        painter.setBrush(QColor(current_fill))
        painter.drawRect(box)

        if checked:
            painter.setPen(QPen(QColor(tick if enabled else disabled), 2))
            painter.drawLine(4, 9, 7, 12)
            painter.drawLine(7, 12, 13, 6)

        painter.end()


class FindingsEntityRow(QFrame):
    """Softer entity row for the category inspection list."""

    clicked = Signal(int)
    check_toggled = Signal(int, bool)

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

        center = QVBoxLayout()
        center.setSpacing(3)

        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        self._name_lbl = QLabel(entity.get("name", "Unknown"))
        self._name_lbl.setStyleSheet("font-size: 14px; font-weight: 760;")
        self._name_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        title_row.addWidget(self._name_lbl, stretch=1)

        self._risk_badge = Badge(entity.get("risk", "Review"), _status_variant(entity.get("risk", "Review")))
        title_row.addWidget(self._risk_badge, alignment=Qt.AlignVCenter)
        center.addLayout(title_row)

        self._meta_lbl = QLabel()
        self._meta_lbl.setObjectName("Muted")
        self._meta_lbl.setStyleSheet(
            "font-family: 'JetBrains Mono'; font-size: 11px;"
        )
        self._meta_lbl.setWordWrap(False)
        self._meta_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
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

    def rebind(self, source_row: int, entity: dict, checked: bool):
        """Repoint a pooled row at a new entity instead of recreating it."""
        self._source_row = source_row
        self._selected = False
        self._hovered = False
        self.update_entity(entity, checked)

    def update_entity(self, entity: dict, checked: bool):
        self._entity = entity
        risk = entity.get("risk", "Review")
        is_duplicate = entity.get("entity_type") == "duplicate_group"
        self._name_lbl.setText(_duplicate_title(entity) if is_duplicate else entity.get("name", "Unknown"))
        self._risk_badge.set_badge(risk, _status_variant(risk))
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
        self._risk_btns: dict[str, QPushButton] = {}
        for risk in ("Safe", "Optional", "Review", "Protected"):
            btn = QPushButton(risk)
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

    def set_category(self, category: str, entities: list, cap_notice: str = ""):
        """Set the category and entities to display."""
        self.category = category
        self.entities = entities
        self._selected_path = ""
        self._update_selection_display()
        self._clear_detail_sidebar()
        self._btn_select_all.setVisible(category in _BULK_SELECT_CATEGORIES)

        color = _get_category_color(category)

        self._title_lbl.setText(category.upper())
        self._title_lbl.setStyleSheet(
            f"font-family: 'Silkscreen', 'JetBrains Mono'; font-size: 14px; letter-spacing: 3px; color: {color};"
        )

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

    def _apply_risk_filter(self):
        active = {r for r, btn in self._risk_btns.items() if btn.isChecked()}
        self._proxy.set_risk_filter(active if len(active) < len(self._risk_btns) else None)
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
        for risk, btn in self._risk_btns.items():
            if btn.isChecked():
                color = _status_color(risk)
                btn.setStyleSheet(
                    f"font-size: 10px; padding: 5px 12px; color: {color}; "
                    f"background: {panel}; border: 1px solid {border}; border-radius: 2px;"
                )
            else:
                btn.setStyleSheet(
                    f"font-size: 10px; padding: 5px 12px; color: {faint}; "
                    f"background: transparent; border: 1px solid {quiet}; border-radius: 2px;"
                )

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
            QTimer.singleShot(0, self._ensure_selected_row_visible)
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
        live = self._find_live_item(path)
        if live is None:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(
                self,
                tr("File not available"),
                tr("This file is not part of the current scan results."),
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
            self._sel_count_lbl.setText(f"{count} selected")
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
            self._show_toast(f"✓  {n} item(s) moved to Recycle Bin · {freed} freed")
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

        reply = QMessageBox.question(
            self, tr("Deep Uninstall"),
            tr("Run the official uninstaller for {name}?\n\n"
               "This launches the application's own uninstaller. Follow its "
               "prompts to finish removal.").format(name=name),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        started, message = launch_uninstaller(uninstall_cmd)
        if self._scan_state and hasattr(self._scan_state, "log_line"):
            self._scan_state.log_line.emit(f"[uninstall] {name}: {message}")
        if started:
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
            self._show_toast(f"Uninstaller launched · {name} — re-scan to confirm removal")
        else:
            QMessageBox.warning(self, tr("Deep Uninstall failed"), message)

    def _show_toast(self, message: str, ms: int = 5000):
        self._sel_size_lbl.setText(message)
        self._sel_size_lbl.setStyleSheet(
            "font-family: 'JetBrains Mono'; font-size: 11px; "
            f"color: {get_palette().get('safe', '#7aa88a')};"
        )
        QTimer.singleShot(ms, self._clear_toast)

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

    def _rebuild_entity_rows(self):
        """Repopulate the list by reusing pooled row widgets.

        Filtering/sorting/searching only rebinds existing widgets to the new
        set of entities (and hides any surplus) instead of destroying and
        recreating the whole list, which is what made filter clicks janky.
        """
        visible = self._proxy.rowCount()
        self._list_count_lbl.setText(tr("// {n:,} visible", n=visible))
        self._row_widgets.clear()

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
        for proxy_row in range(visible):
            source_index = self._proxy.mapToSource(self._proxy.index(proxy_row, COL_NAME))
            sr = source_index.row()
            entity = self._model.get_entity(sr)
            if not entity:
                continue
            checked = self._model.is_checked(sr)
            if idx < len(self._row_pool):
                row = self._row_pool[idx]
                row.rebind(sr, entity, checked)
                row.setVisible(True)
            else:
                row = FindingsEntityRow(sr, entity, checked)
                row.clicked.connect(self._select_source_row)
                row.check_toggled.connect(self._set_checked_state)
                self._row_pool.append(row)
                # Insert just before the trailing stretch so order stays stable.
                self._list_layout.insertWidget(self._list_layout.count() - 1, row)
            path = entity.get("path", "")
            is_selected = bool(path) and path == self._selected_path
            row.set_selected(is_selected)
            selected_visible = selected_visible or is_selected
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

        # Spinner
        self._spinner_lbl = QLabel("\u25d0")
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
                if hasattr(cv, "_detail_widget") and cv._detail_widget is not None:
                    cv._detail_widget._apply_block_styles()
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

        self._main_layout.addWidget(self._stack)

        # Show appropriate state initially
        self._update_for_current_state()

    def _build_dashboard_container(self) -> QWidget:
        """Build the dashboard — StorageOverviewWidget is the sole visualization."""
        self._overview_view = StorageOverviewWidget(
            on_category_click=self._on_category_click
        )
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
        entity_count = self._scan_state.entity_count

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
        normalized = category_name.strip()
        if normalized.lower() != "unknown":
            normalized = normalized.title()

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
        cat = (entity.get("category") or "Unknown").strip()
        cat_norm = cat.title() if cat.lower() != "unknown" else "Unknown"
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
                cat_norm = cat.strip().title() if cat.lower() != "unknown" else "Unknown"
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
            cat = (item.get("category") or "Unknown").strip()
            if cat.lower() != "unknown":
                cat = cat.title()
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
        cat_norm = category.strip().title()
        matched = []
        for e in all_items:
            if (e.get("category", "Unknown") or "Unknown").strip().title() == cat_norm:
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
