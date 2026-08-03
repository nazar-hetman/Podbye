"""SmartEntity model — grouped, AI-classified folder/content entities.

A SmartEntity represents a meaningful group of files detected during Smart scan:
an application, content collection, cache, project, or technical artifact.

Instead of exposing raw file listings, Smart mode groups items into entities
that users can reason about at a higher level.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.models.finding import _format_size, _format_age


# ── Entity types ──────────────────────────────────────────────────

ENTITY_TYPES = {
    # Applications (hierarchy: Installed / Portable / Installers)
    "application":      "Installed Application",
    "portable_app":     "Portable Application",
    "installer":        "Installer Package",
    "installer_group":  "Installer Collection",
    "installer_cache":  "Installer / Update Cache",
    "application_data": "Application Data",
    "user_profile":     "User Profile",
    "vm_storage":       "Virtual Machine Storage",
    
    # Games (platform-specific)
    "game":             "Game Installation",
    "game_cache":       "Game Shader/Cache Data",
    "game_saves":       "Game Save Data",

    # Development (hierarchy: Projects / Build Artifacts / Dependencies)
    "dev_project":      "Development Project",
    "dev_artifacts":    "Build Artifacts / Dependencies",
    "development_environment": "Development Environment",
    "venv":             "Python Virtual Environment",
    "node_modules":     "Node.js Dependencies",
    "build_folder":     "Build Output",

    # AI / ML (hierarchy: Models / Checkpoints / Datasets / Cache)
    "ai_models":        "AI/ML Model Files",
    "ai_cache":         "AI Runtime Cache",

    # Media (hierarchy: Images / Videos / Audio / Creative Projects)
    "photo_collection": "Images / Photos",
    "video_collection": "Videos / Movies",
    "audio_collection": "Audio / Music",
    "creative_project": "Creative Project Files",
    "media_collection": "Mixed Media Collection",

    # Archives & Backups
    "archive_group":    "Archive Files",
    "backup_group":     "Backup Files",
    "dataset":          "Dataset / Data Files",

    # Documents
    "document_folder":  "Documents Folder",

    # Downloads — one entity per downloaded item, never a merged blob
    "download_item":    "Downloaded Item",

    # Cache & Temp
    "cache_folder":     "Cache / Temp Folder",
    "temp_folder":      "Temporary Files",
    "shader_cache":     "Shader Cache",
    "log_folder":       "Log Files",

    # Browser Data
    "browser_profile":  "Browser Profile/Data",

    # Databases & Saves
    "database":         "Database Files",

    # Protected / System
    "protected_system": "Protected System Path",

    # Fallback (ensures NO loose files)
    "mixed_folder":     "Mixed Content Folder",
    "unknown_folder":   "Unclassified Folder",
    "loose_files":      "Loose Files Bucket",

    # Duplicates (detected by content hash)
    "duplicate_group":  "Duplicate File Group",
}


# ── Risk mapping ──────────────────────────────────────────────────

_ENTITY_RISK = {
    # Applications
    "application":      "Review",
    "portable_app":     "Optional",
    "installer":        "Optional",
    "installer_group":  "Optional",
    "installer_cache":  "Review",
    "application_data": "Review",
    "user_profile":     "Review",
    "vm_storage":       "Review",
    
    # Games
    "game":             "Review",
    "game_saves":       "Review",
    "game_cache":       "Safe",
    
    # Development
    "dev_project":      "Review",
    "dev_artifacts":    "Safe",
    "development_environment": "Review",
    "venv":             "Safe",
    "node_modules":     "Safe",
    "build_folder":     "Safe",
    
    # AI / ML
    "ai_models":        "Review",
    "ai_cache":         "Safe",
    
    # Media
    "photo_collection": "Review",
    "video_collection": "Review",
    "audio_collection": "Review",
    "creative_project": "Review",
    "media_collection": "Review",
    
    # Archives & Backups
    "archive_group":    "Optional",
    "backup_group":     "Optional",
    "dataset":          "Review",
    
    # Documents
    "document_folder":  "Review",

    # Downloads — the user decides; a download may be precious or disposable
    "download_item":    "Review",
    
    # Cache & Temp
    "cache_folder":     "Safe",
    "temp_folder":      "Safe",
    "shader_cache":     "Safe",
    "log_folder":       "Safe",
    
    # Browser
    "browser_profile":  "Review",
    
    # Databases
    "database":         "Review",
    
    # System
    "protected_system": "Protected",
    
    # Fallback
    "mixed_folder":     "Review",
    "unknown_folder":   "Review",
    "loose_files":      "Review",

    # Duplicates
    "duplicate_group":  "Optional",
}


# ── Display-category mapping ──────────────────────────────────────
# Maps entity_type → the category shown in Findings filter chips.
# Hierarchy: Applications · Games · Media · Dev Artifacts · AI/ML ·
# Archives · Documents · Cache & Temp · Databases & Saves ·
# Browser Data · System Logs · System · Unknown (never loose files).
_CATEGORY_BY_TYPE = {
    # Applications
    "application": "Applications",
    "portable_app": "Applications",
    "application_data": "Application Data",
    "user_profile": "User Profile",
    "vm_storage": "Virtual Machines",

    # Installers — user-facing installer files/packages only.
    "installer": "Installers",
    "installer_group": "Installers",
    # Vendor update/download staging (e.g. NVIDIA Downloader) is regenerable
    # cache that lives inside an app, not a user installer — show it as cache.
    "installer_cache": "Cache & Temp",

    # Games
    "game": "Games",
    "game_cache": "Cache & Temp",
    # NOTE: game_saves is under Databases & Saves below

    # Development
    "dev_project": "Dev Artifacts",
    "dev_artifacts": "Dev Artifacts",
    "development_environment": "Dev Artifacts",
    "venv": "Dev Artifacts",
    "node_modules": "Dev Artifacts",
    "build_folder": "Dev Artifacts",

    # AI / ML
    "ai_models": "AI / ML",
    "ai_cache": "Cache & Temp",

    # Media — split by content type; media_collection is the mixed bucket
    "photo_collection": "Images",
    "video_collection": "Videos",
    "audio_collection": "Audio",
    "creative_project": "Creative Projects",
    "media_collection": "Media",

    # Archives & Backups
    "archive_group": "Archives",
    "backup_group": "Archives",
    # dataset = photogrammetry / research data — belongs with AI / ML, not Archives
    "dataset": "AI / ML",

    # Documents
    "document_folder": "Documents",

    # Downloads
    "download_item": "Downloads",

    # Browser Data
    "browser_profile": "Browser Data",

    # Cache & Temp
    "cache_folder": "Cache & Temp",
    "temp_folder": "Cache & Temp",
    "shader_cache": "Cache & Temp",

    # System Logs
    "log_folder": "System Logs",

    # Databases · Saves — split apart, because one bucket held two things of
    # opposite value to the user. Game saves are irreplaceable and the whole
    # point is to never touch them; a stray app database is disposable-ish
    # app state. Together they made a category no single sentence could
    # describe, and no advice could apply to.
    "database": "Databases",
    "game_saves": "Saves",

    # System
    "protected_system": "System",

    # Fallback (ensures NO loose files)
    "mixed_folder": "Unknown",
    "unknown_folder": "Unknown",
    "loose_files": "Unknown",

    # Duplicates
    "duplicate_group": "Duplicates",
}

# Types where the type must keep winning over any origin. Protection has to
# stay visible wherever the item sits, and a duplicate group deliberately spans
# several locations at once, so filing it under one of them would be a lie.
_ORIGIN_EXEMPT_TYPES = frozenset({"protected_system", "duplicate_group"})


# Entity types whose contents are auto-regenerated by the owning app/OS.
_AUTO_REGEN_TYPES = frozenset({
    "cache_folder", "temp_folder", "shader_cache",
    "log_folder", "ai_cache", "game_cache",
})

# Entity types produced by development tooling — safe to regenerate.
_DEV_GENERATED_TYPES = frozenset({
    "dev_artifacts", "venv", "node_modules", "build_folder",
})


# ── Actionability ─────────────────────────────────────────────────
# What action actually makes sense for an entity — this gates the UI so the
# user is never offered a whole-folder "delete" on something where that is the
# wrong (or dangerous) operation. Risk explains *how careful* to be;
# actionability decides *what the action even is*.
#
#   recycle      delete the whole folder/group — that is the intended action
#                (caches, build output, installers, archives, duplicate extras)
#   uninstall    installed software — remove via the uninstaller, not by
#                recycling the install tree
#   review_only  personal / mixed / ambiguous content — Vigil must NOT offer a
#                whole-folder delete; it helps the user look inside instead
#   protected    system-critical — no destructive action at all

_RECYCLE_TYPES = frozenset({
    "cache_folder", "temp_folder", "shader_cache", "log_folder",
    "ai_cache", "game_cache",
    "dev_artifacts", "venv", "node_modules", "build_folder",
    "installer", "installer_group", "installer_cache",
    "archive_group", "backup_group", "ai_models",
    "duplicate_group",
})

_UNINSTALL_TYPES = frozenset({
    "application", "portable_app", "game",
})

# Personal data, irreplaceable content, or folders too mixed/ambiguous to treat
# as one disposable unit. Deleting the whole thing is rarely what the user wants
# — e.g. a folder of documents AND videos can't be "deleted as Documents".
_REVIEW_ONLY_TYPES = frozenset({
    "document_folder",
    "photo_collection", "video_collection", "audio_collection",
    "creative_project", "media_collection",
    "user_profile", "application_data", "browser_profile",
    "database", "game_saves", "dataset",
    "vm_storage", "dev_project",
    "mixed_folder", "unknown_folder", "loose_files",
})


def actionability_for_type(entity_type: str, risk: str = "") -> str:
    """Classify what action an entity supports. Safest default is review_only."""
    if entity_type == "protected_system" or risk == "Protected":
        return "protected"
    if entity_type in _UNINSTALL_TYPES:
        return "uninstall"
    if entity_type in _REVIEW_ONLY_TYPES:
        return "review_only"
    if entity_type in _RECYCLE_TYPES:
        return "recycle"
    return "review_only"


@dataclass
class SmartEntity:
    """A grouped, meaningful entity detected from filesystem scan results."""

    # Identity
    path: str                           # root folder path
    name: str                           # display name (folder basename or detected app name)
    entity_type: str                    # key from ENTITY_TYPES

    # Metrics
    size_bytes: int = 0
    file_count: int = 0
    folder_count: int = 0

    # Classification
    risk: str = ""
    risk_reason: str = ""
    summary: str = ""                   # human-readable summary line
    confidence: str = "heuristic"       # heuristic | ai
    confidence_score: float = 0.0       # 0.0-1.0 — detection signal strength

    # AI fields
    ai_status: str = "none"             # none | disabled | pending | analyzing | ready | failed | cancelled
    ai_explanation: str = ""
    ai_error: str = ""
    ai_model: str = ""
    ai_language: str = ""               # language used for AI explanation
    ai_updated_at: float = 0.0

    # Metadata
    modified: float = 0.0              # most recent mtime in the entity
    accessed: float = 0.0              # most recent atime
    children_sample: list = field(default_factory=list)  # sample child paths for AI context
    
    # Ownership & Relationship
    parent_app: str = ""                # Name of owning application (if internal component)
    parent_app_path: str = ""          # Path of owning application
    is_internal: bool = False          # True if this is an internal app component
    depth: int = 0                      # Nesting depth from scan root
    
    # App Metadata (for installed applications)
    app_version: str = ""               # Version string
    app_publisher: str = ""             # Publisher/Developer
    install_date: str = ""              # Installation date if available
    uninstall_string: str = ""          # Registry uninstaller command (Deep Uninstall)
    
    # Cloud sync
    cloud_sync_provider: str = ""        # "OneDrive" | "Dropbox" | "Google Drive" | "" (none)

    # Age-based heuristics
    age_boost: float = 0.0               # 0.0 | 0.2 (2y+) | 0.4 (5y+) — only for eligible types

    # Duplicate groups
    dup_reclaimable: int = 0             # bytes reclaimable from duplicate_group (all-but-one copy)
    duplicate_locations: list = field(default_factory=list)  # full duplicate location metadata
    removable_duplicate_paths: list = field(default_factory=list)  # duplicate files approved for cleanup

    # Loose / grouped buckets: the actual files this entity stands for, so
    # cleanup targets those files instead of the entity's (folder/root) path.
    removable_file_paths: list = field(default_factory=list)

    # Vigil's own install / data directory. Carried as a flag rather than baked
    # into risk_reason so the UI can phrase it in the user's language, and
    # re-phrase it when the language changes mid-session.
    is_self: bool = False

    # The place this entity came from, when the place matters more than the
    # type ("Downloads", "Desktop"). See the category property.
    origin: str = ""

    # Computed
    size: str = ""
    age: str = ""

    def __post_init__(self):
        if not self.risk:
            self.risk = _ENTITY_RISK.get(self.entity_type, "Review")
            if not self.risk_reason:
                self.risk_reason = f"entity type: {self.entity_type}"
        # Cloud-sync entities are never Safe — minimum risk is Review
        if self.cloud_sync_provider and self.risk == "Safe":
            self.risk = "Review"
            self.risk_reason = f"cloud-synced ({self.cloud_sync_provider})"
        if not self.size:
            self.size = _format_size(self.size_bytes)
        if not self.age and self.modified:
            self.age = _format_age(self.modified)
        if not self.summary:
            type_label = ENTITY_TYPES.get(self.entity_type, self.entity_type)
            self.summary = f"{type_label} · {self.file_count:,} files · {self.size}"

    @property
    def cache_key(self) -> str:
        """Stable cache key for AI explanations."""
        norm = self.path.replace("\\", "/").lower()
        return f"entity|{norm}|{self.size_bytes}|{int(self.modified)}"

    @property
    def category(self) -> str:
        """The display category for filter chips: where to look, then what it is.

        Type decides it (see _CATEGORY_BY_TYPE) unless the entity carries an
        origin, because for some places the location is the more useful answer.
        Downloads was split across four categories at once — the folders under
        it in Downloads, its loose archives in Archives, its .exe/.msi files in
        Installers, everything else in Unknown — so no single view showed the
        user their Downloads folder.

        Only the grouping moves. entity_type still drives risk, actionability
        and the detail panel, so an installer in Downloads is still an installer.
        """
        if self.origin and self.entity_type not in _ORIGIN_EXEMPT_TYPES:
            return self.origin
        return _CATEGORY_BY_TYPE.get(self.entity_type, "Unknown")

    @property
    def actionability(self) -> str:
        """recycle | uninstall | review_only | protected — see actionability_for_type."""
        return actionability_for_type(self.entity_type, self.risk)

    @property
    def confidence_label(self) -> str:
        """Human-readable bucket for confidence_score.

        Verified ≥ 0.9 · Strong ≥ 0.7 · Likely ≥ 0.45 · Uncertain below.
        """
        score = self.confidence_score
        if score >= 0.9:
            return "Verified"
        if score >= 0.7:
            return "Strong"
        if score >= 0.45:
            return "Likely"
        return "Uncertain"

    def to_dict(self) -> dict:
        """Convert to dict for the Findings screen."""
        type_label = ENTITY_TYPES.get(self.entity_type, self.entity_type)
        # Reclaimable: duplicates are special (only extra copies); everything
        # else uses the shared formula so both scan modes agree on the number.
        if self.entity_type == "duplicate_group":
            reclaimable = self.dup_reclaimable if self.risk == "Optional" else 0
        else:
            from app.models.risk import reclaimable_bytes
            reclaimable = reclaimable_bytes(
                self.risk, self.size_bytes, age_boost=self.age_boost
            )
        display_size = self.size
        if self.entity_type == "duplicate_group" and self.dup_reclaimable:
            display_size = _format_size(self.dup_reclaimable)

        return {
            # Core fields (compatible with Finding.to_dict keys)
            "category": self.category,
            "path": self.path,
            "name": self.name,
            "is_dir": True,
            "size": display_size,
            "size_bytes": self.size_bytes,
            "reclaimable_bytes": reclaimable,
            "age": self.age,
            "risk": self.risk,
            "source_rule": f"entity detection: {self.entity_type}",
            "risk_reason": self.risk_reason,
            "why": self._why_text(type_label),
            "recommendation": self._recommendation(),
            "ai_status": self.ai_status,
            "ai_explanation": self.ai_explanation,
            "ai_error": self.ai_error,
            "ai_model": self.ai_model,
            "ai_language": self.ai_language,
            # Entity-specific extras
            "is_entity": True,
            "entity_type": self.entity_type,
            "entity_type_label": type_label,
            "actionability": self.actionability,
            "file_count": self.file_count,
            "folder_count": self.folder_count,
            "summary": self.summary,
            "confidence": self.confidence,
            "confidence_score": self.confidence_score,
            "confidence_label": self.confidence_label,
            "cloud_sync_provider": self.cloud_sync_provider,
            "last_access": self._safe_date(self.accessed),
            "first_seen": self._safe_date(self.modified),
            "install_date": self.install_date,
            "app_version": self.app_version,
            "app_publisher": self.app_publisher,
            "uninstall_string": self.uninstall_string,
            # Age / duplicate fields
            "modified": self.modified,
            "accessed": self.accessed,
            "age_boost": self.age_boost,
            "dup_reclaimable": self.dup_reclaimable,
            "children_sample": list(self.children_sample),
            "duplicate_locations": list(self.duplicate_locations),
            "removable_duplicate_paths": list(self.removable_duplicate_paths),
            "removable_file_paths": list(self.removable_file_paths),
            "is_self": self.is_self,
            "origin": self.origin,
        }

    def _why_text(self, type_label: str) -> str:
        reason = self._why_found_text()
        impact = self._removal_impact_text()
        return f"What it is: {type_label}. Why found: {reason}. If removed: {impact}."

    def _why_found_text(self) -> str:
        reason = (self.risk_reason or "").strip().rstrip(".")
        if reason and not reason.lower().startswith("entity type:"):
            return reason

        if self.entity_type == "duplicate_group":
            return "Vigil found identical file content in multiple locations"
        if self.entity_type in _AUTO_REGEN_TYPES:
            return "its name or contents match cache, temporary, or log data that apps usually recreate"
        if self.entity_type in _DEV_GENERATED_TYPES:
            return "its name or contents match generated development dependencies or build output"
        if self.entity_type == "dev_project":
            return "project markers or source folders indicate a development project"
        if self.entity_type in {"application_data", "browser_profile", "user_profile"}:
            return "the path looks like app, browser, or user support data"
        if self.entity_type in {"unknown_folder", "mixed_folder", "loose_files"}:
            return "it has enough content to review, but Vigil could not identify a stronger owner"
        return "its folder name, file types, or known markers match this finding type"

    def _removal_impact_text(self) -> str:
        if self.risk == "Protected" or self.entity_type == "protected_system":
            return "Windows or an installed app may stop working correctly"
        if self.entity_type == "duplicate_group":
            return "extra copies can free space while one copy is kept, but remove only copies you recognize"
        if self.entity_type in _AUTO_REGEN_TYPES:
            return "the owning app may recreate it, though cached state or log history can be lost"
        if self.entity_type in _DEV_GENERATED_TYPES:
            return "the project or tool may need to download dependencies or rebuild files again"
        if self.entity_type in {"dev_project", "creative_project"}:
            return "source or project files may be deleted"
        if self.entity_type in {
            "application", "portable_app", "application_data", "user_profile",
            "browser_profile", "game", "game_saves", "database", "vm_storage",
            "ai_models", "dataset",
        }:
            return "app data, saved work, profiles, models, or user content may be lost"
        if self.entity_type in {
            "photo_collection", "video_collection", "audio_collection",
            "media_collection", "document_folder",
        }:
            return "personal media or documents may be deleted"
        if self.entity_type in {"installer", "installer_group", "installer_cache", "archive_group", "backup_group"}:
            return "installers, archives, or backups you might need later may be deleted"
        if self.risk == "Review":
            return "review it first because it may still belong to you or an app"
        if self.risk == "Optional":
            return "space is freed, but only remove it if you no longer need it"
        return "space is freed and the item is expected to be safe to regenerate or remove"

    def _recommendation(self) -> str:
        if self.risk == "Protected":
            return "Do not remove — system-critical or protected"
        if self.entity_type == "duplicate_group" and self.risk == "Optional":
            return "Optional cleanup — keep one copy and remove the extras"
        if self.risk == "Review":
            return "Review before cleanup — may still matter to you or an app"
        if self.risk == "Optional":
            return "Optional cleanup — remove if you no longer need it"
        if self.risk == "Safe":
            if self.entity_type in _AUTO_REGEN_TYPES:
                return "Remove — auto-regenerated"
            if self.entity_type in _DEV_GENERATED_TYPES:
                return "Safe cleanup — generated by development tools"
            return "Likely safe to remove"
        if self.age_boost >= 0.4:
            return "Strong cleanup candidate — not modified in 5+ years"
        if self.age_boost >= 0.2:
            return "Cleanup candidate — not modified in 2+ years"
        return "Manual review required"

    @staticmethod
    def _safe_date(ts: float) -> str:
        try:
            if ts <= 0:
                return "—"
            return time.strftime("%Y-%m-%d", time.localtime(ts))
        except Exception:
            return "—"
