"""Entity detector — groups raw findings into SmartEntities.

Semantic Storage Intelligence System
====================================
Transforms raw file scans into meaningful semantic entities.

Core Rule: Every scanned item MUST belong to:
  1. Known semantic entity
  2. Parent folder entity
  3. Content bucket
  4. Unknown grouped entity

NO loose files should remain invisible in Findings.

Categories Implemented:
- Applications (Installed, Portable, Installers)
- Games (Steam, Epic, GOG, Ubisoft, Battle.net, Xbox)
- Media (Images, Videos, Audio, Creative Projects)
- Archives & Backups
- Development (Projects, Build Artifacts, Dependencies)
- AI / ML (Models, Checkpoints, Datasets)
- Documents
- Cache & Temp
- Databases & Saves
- Unknown (auto-grouped, never loose)

Performance: uses prefix-indexed dictionaries instead of linear scans
for O(k) child gathering and O(k) tree claiming where k is the number
of children rather than O(n) over all findings.
"""
from __future__ import annotations

import json
import os
import re
import time as _time
from collections import defaultdict
from pathlib import Path
from typing import Optional

from app.models.finding import Finding, _format_size
from app.models.smart_entity import SmartEntity


# ═══════════════════════════════════════════════════════════════════════════
# CLASSIFICATION RULE DATA
# ═══════════════════════════════════════════════════════════════════════════
# The large vendor / application / directory knowledge tables live in an
# external JSON file (classification_rules.json, next to this module) so
# they can be maintained and extended without touching detection logic.
# ═══════════════════════════════════════════════════════════════════════════

_RULES_PATH = Path(__file__).with_name("classification_rules.json")


def _load_classification_rules() -> dict:
    """Load the external classification rule tables.

    A failure here is fatal — entity detection cannot run without these
    tables — so the error is raised rather than silently swallowed.
    """
    try:
        with open(_RULES_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            f"Vigil: cannot load classification rules from {_RULES_PATH}: {exc}"
        ) from exc


_RULES = _load_classification_rules()


# ═══════════════════════════════════════════════════════════════════════════
# WINDOWS REGISTRY INTEGRATION (for installed app detection)
# ═══════════════════════════════════════════════════════════════════════════

def _get_installed_programs() -> dict[str, dict]:
    """Query Windows registry for installed programs.
    
    Returns dict mapping normalized install path → program info.
    """
    installed: dict[str, dict] = {}
    
    try:
        import winreg
        
        # Registry locations for installed software
        reg_paths = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        ]
        
        for hkey, path in reg_paths:
            try:
                with winreg.OpenKey(hkey, path) as key:
                    for i in range(winreg.QueryInfoKey(key)[0]):
                        try:
                            subkey_name = winreg.EnumKey(key, i)
                            with winreg.OpenKey(key, subkey_name) as subkey:
                                try:
                                    name, _ = winreg.QueryValueEx(subkey, "DisplayName")
                                    install_loc = ""
                                    try:
                                        install_loc, _ = winreg.QueryValueEx(subkey, "InstallLocation")
                                    except OSError:
                                        pass
                                    
                                    publisher = ""
                                    try:
                                        publisher, _ = winreg.QueryValueEx(subkey, "Publisher")
                                    except OSError:
                                        pass
                                    
                                    if install_loc:
                                        norm_path = install_loc.replace("\\", "/").lower().rstrip("/")
                                        installed[norm_path] = {
                                            "name": name,
                                            "publisher": publisher,
                                            "path": install_loc,
                                        }
                                except OSError:
                                    pass
                        except OSError:
                            pass
            except OSError:
                pass
                
    except ImportError:
        pass  # Not on Windows
    
    return installed


def _is_installed_app(path: str, installed_apps: dict[str, dict]) -> Optional[dict]:
    """Check if path matches a known installed application.

    Uses a walk-up algorithm: start at the query path and remove one segment
    at a time until a match is found or we reach the drive root.  This is
    O(path_depth) instead of O(n_installed_apps), which matters when called
    for every scanned directory (potentially tens of thousands of calls).

    Returns the most specific matching app info dict, or None.
    """
    norm = path.replace("\\", "/").lower().rstrip("/")
    while norm:
        if norm in installed_apps:
            return installed_apps[norm]
        if "/" not in norm:
            break
        norm = norm.rsplit("/", 1)[0]
    return None


# ═══════════════════════════════════════════════════════════════════════════
# GAME PLATFORM DETECTION
# ═══════════════════════════════════════════════════════════════════════════

def _detect_game_platform(path: str) -> Optional[str]:
    """Detect which game platform a path belongs to.

    Returns platform name (steam, epic, gog, ubisoft, battlenet, xbox) or None.

    Uses path-SEGMENT matching (not substring) to avoid false positives such as
    "windoWsTeam" containing "steam" or "SteamCmd" being a game installation.
    """
    norm = path.replace("\\", "/").lower()
    parts = set(norm.split("/"))

    # Steam — exact segment match; "steamcmd" / "steamworks" are tools, not games
    if "steam" in parts or "steamapps" in parts:
        return "steam"

    # Epic Games — segment or two-word segment
    if "epic games" in norm or "epicgames" in parts:
        return "epic"

    # GOG
    if "gog games" in norm or "gog galaxy" in norm:
        return "gog"

    # Ubisoft
    if "ubisoft" in parts or "uplay" in parts:
        return "ubisoft"

    # Battle.net
    if "battle.net" in parts or "battlenet" in parts:
        return "battlenet"

    # Xbox
    if "xboxgames" in parts or "microsoft games" in norm:
        return "xbox"

    return None


# ── Known application signatures ──────────────────────────────────
# Maps (marker_filename_lower) → (entity_type, display_name_template)
# display_name_template: None means use parent folder name

_APP_MARKERS: dict = {
    _k: (_v["type"], _v["name"]) for _k, _v in _RULES["app_markers"].items()
}

# Directory names that are always entity roots.
# Order does not matter — this is a dict lookup, not a priority list.
# Notes:
#  - 'bin', 'debug', 'release', 'obj', 'src' are intentionally absent:
#    too generic — they'd claim C:/ffmpeg/bin before Pass 2b sees ffmpeg.exe.
#  - '.env' intentionally absent: it's an env-vars file (KEY=value), not a venv.
#  - 'profiles' intentionally absent: too broad (browsers, apps, VMs all use it).
#  - 'dump'/'dumps' are log_folder: crash dumps are diagnostic logs, not backups.
_DIR_ENTITY_MAP: dict = dict(_RULES["dir_entity_map"])

# Extensions for content classification
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".raw",
               ".svg", ".webp", ".heic", ".heif", ".cr2", ".nef", ".arw",
               ".dng", ".rw2", ".orf", ".pef", ".xmp", ".psd", ".ai"}
_VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".wmv", ".flv", ".webm",
               ".m4v", ".ts", ".mts", ".m2ts", ".vob", ".mpg", ".mpeg"}
_AUDIO_EXTS = {".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a",
               ".opus", ".alac", ".aiff"}
# Creative project files (Media → Projects)
_PROJECT_EXTS = {".prproj", ".aep", ".aet", ".drp", ".blend", ".psd",
                 ".ai", ".xd", ".sketch", ".fig", ".afdesign", ".afphoto"}
_PROJECT_DIRS = {"after effects projects", "premiere pro projects", 
                 "davinci resolve projects", "blender projects"}
_DOC_EXTS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
             ".txt", ".md", ".csv", ".rtf", ".odt", ".ods", ".epub"}
_ARCHIVE_EXTS = {".zip", ".rar", ".7z", ".tar", ".gz", ".iso", ".bz2", ".xz",
                 ".tgz", ".tar.gz", ".tar.bz2", ".tar.xz", ".cab"}
_INSTALLER_EXTS = {".exe", ".msi", ".msix", ".appx", ".dmg"}
_MODEL_EXTS = {".gguf", ".bin", ".safetensors", ".pt", ".pth", ".onnx",
               ".ckpt", ".h5", ".tflite", ".ggml"}
_BACKUP_EXTS = {".bak", ".old", ".backup", ".orig", ".swp", ".sav"}
_LOG_EXTS = {".log", ".log.1", ".log.gz"}
_DATABASE_EXTS = {".sqlite", ".sqlite3", ".db", ".mdb", ".accdb", ".ldf", ".mdf"}
_PHOTOGRAMMETRY_EXTS = {".las", ".laz", ".ply", ".obj", ".e57", ".xyz",
                        ".tif", ".tiff", ".ecw"}
_VM_DISK_EXTS = {".vdi", ".vmdk", ".vhd", ".vhdx", ".qcow2", ".qed", ".vbox"}

# Browser profile path keywords
_BROWSER_KEYWORDS = {"chrome", "firefox", "edge", "opera", "brave", "vivaldi",
                     "chromium", "mozilla", "safari"}

# Cache/temp path keywords
_CACHE_KEYWORDS = {"cache", "caches", "tmp", "temp", "thumbcache",
                   "thumbnails", "httpcache", "webcache", "diskcache"}

# Known cache/installer/log parent app hints: path segment → friendly description
# Used to annotate "Cache for X", "Installer – X", "Logs – X" rather than bare names
_CACHE_SOURCE_HINTS: dict = dict(_RULES["cache_source_hints"])

# ── Protected path detection ─────────────────────────────────────

# True system-critical paths that are always protected
_PROTECTED_DIR_NAMES = {
    "windows", "system32", "syswow64", "winsxs",
    "programdata", "recovery", "boot",
    "$windows.~bt", "$windows.~ws",
    "msocache", "perfmon", "perflogs",
}

# Container paths that should NOT become System entities if they contain
# meaningful child entities (like applications). These are treated as 
# organizational folders, not protected system paths.
_CONTAINER_DIR_NAMES = {
    "program files", "program files (x86)",
}

_PROTECTED_APPDATA_DIRS = {
    "microsoft", "local settings", "credential",
    "identities", "crypto", "protect", "systemcertificates",
    "packages",  # UWP store app data
}


# Folder names that are too generic to stand alone as entity names.
# When an entity has one of these as its display name, _qualify_folder_name()
# appends a meaningful context qualifier from the path.
_GENERIC_FOLDER_NAMES = {
    # Documents
    "documents", "document", "docs", "doc",
    # Media
    "photos", "photo", "pictures", "picture", "images", "image", "screenshots",
    "videos", "video", "movies", "movie", "films", "recordings",
    "music", "audio", "songs", "song", "podcasts",
    # Downloads / transfers
    "downloads", "download", "received files",
    # Backups / exports
    "backup", "backups", "bak", "archive", "archives", "old", "export", "exports",
    # Cache / temp
    "cache", "caches", "tmp", "temp", "temporary",
    # Logs
    "logs", "log",
    # Installer / update folders
    "installer", "installers",
    # Crash dumps / diagnostics
    "dump", "dumps", "crash", "crashes",
    # Build artifacts — qualify with project name so "build" → "Build – MyApp"
    "build", "builds", "dist", "out", "output", "outputs", "target",
    # Dev dependencies — qualify with project name
    "node_modules", "venv", "vendor",
    # Generic catch-alls
    "data", "files", "misc", "content", "media",
}

# Path segments that are NOT useful as qualifiers — too generic or system-owned.
_QUALIFIER_SKIP_SEGS = {
    # System / infrastructure
    "users", "user", "home", "homes",
    "appdata", "local", "roaming", "localappdata", "locallow",
    "programdata", "all users", "default", "default user",
    "program files", "program files (x86)", "programfiles",
    "public", "shared", "common", "default",
    # Known generic sub-dirs (themselves in _GENERIC_FOLDER_NAMES)
    "documents", "pictures", "music", "videos", "downloads",
    "desktop", "favorites", "contacts", "links", "searches",
    # Windows shell dirs
    "microsoft", "windows", "system32",
}


def _qualify_folder_name(folder_name: str, folder_path: str) -> str:
    """Return a display name with a context qualifier when the folder name is generic.

    Examples:
      "documents"  at c:/users/nazar/documents           → "Documents – Nazar"
      "cache"      at c:/users/nazar/appdata/local/discord/cache → "Cache – Discord"
      "videos"     at c:/steamapps/common/portal2/videos  → "Videos – Portal2"
      "node_modules" (not generic)                        → "node_modules"  (unchanged)
    """
    lower = folder_name.lower()
    if lower not in _GENERIC_FOLDER_NAMES:
        return folder_name

    norm = folder_path.replace("\\", "/").lower()
    parts = norm.split("/")

    # Walk path segments from right-to-left, skipping the folder itself
    for part in reversed(parts[:-1]):
        if not part or part.endswith(":"):          # skip drive letters
            continue
        if part in _QUALIFIER_SKIP_SEGS or part in _GENERIC_FOLDER_NAMES:
            continue
        # Found a meaningful qualifier — title-case it nicely
        qualifier = part.replace("-", " ").replace("_", " ").title()
        return f"{folder_name.title()} – {qualifier}"

    return folder_name.title()


def _infer_cache_source(norm_path: str) -> str:
    """Return a friendly app name if the cache path contains a known app hint.

    Scans each path segment against _CACHE_SOURCE_HINTS.
    Returns '' if nothing recognised — never invents a name.
    """
    parts = norm_path.split("/")
    for part in parts:
        if part in _CACHE_SOURCE_HINTS:
            return _CACHE_SOURCE_HINTS[part]
        # Also try substring match for compound names like "epicgames"
        for key, label in _CACHE_SOURCE_HINTS.items():
            if len(key) >= 5 and key in part:
                return label
    return ""


def _is_protected_path(norm_path: str) -> bool:
    """Check if a path is system-critical or protected."""
    parts = norm_path.split("/")
    for part in parts:
        if part in _PROTECTED_DIR_NAMES:
            return True
    if "appdata" in norm_path:
        for part in parts:
            if part in _PROTECTED_APPDATA_DIRS:
                return True
    return False


_INSTALL_ROOT_NAMES = {
    "program files", "program files (x86)", "programfiles",
    "applications",  # macOS-style installs on some setups
}

# User-profile container names — C:/Users, C:/home.  Only matched at depth 1.
_USER_CONTAINER_NAMES = {"users", "home", "homes"}


def _is_program_files_path(norm_path: str) -> bool:
    """Return True if the path lives inside a Program Files install root AT DEPTH 1.

    Depth-aware to avoid matching backup copies that contain "program files"
    deep in their path (e.g. C:/MATS/{GUID}/FileBackup/C/Program Files (x86)).
    """
    parts = norm_path.split("/")
    # parts[0]=drive, parts[1] must be the install root  (C:/Program Files/...)
    return len(parts) >= 2 and parts[1] in _INSTALL_ROOT_NAMES


def _find_related_app(db_path: str, db_name: str, installed_apps: dict) -> str:
    """Try to find an installed application related to a detached database file.

    Compares the database path components and file stem against known installed
    application names.  Returns a display hint such as
    'Likely database for Irizi Focus F' or '' if nothing matches.

    installed_apps: dict returned by _get_installed_programs() — keys are
    normalised install paths, values are {'name': ..., 'publisher': ..., ...}

    Strategy (in priority order):
    1. Any path segment matches an installed app name (case-insensitive contains)
    2. DB file stem matches an installed app name
    """
    norm_db = db_path.replace("\\", "/").lower()
    db_stem = os.path.splitext(db_name)[0].lower()
    path_parts = norm_db.split("/")

    for app_info in installed_apps.values():
        app_name = app_info.get("name", "") if isinstance(app_info, dict) else ""
        if not app_name:
            continue
        app_lower = app_name.lower()
        # Segment match: e.g. path contains "irizi focus f"
        # Require at least 5 chars to avoid matching common short words
        if any(app_lower in part or part in app_lower for part in path_parts if len(part) >= 5):
            return f"Likely database for {app_name}"
        # Stem match: e.g. "irizi focus.db" vs "Irizi Focus F"
        if db_stem and (db_stem in app_lower or app_lower.startswith(db_stem)):
            return f"Likely database for {app_name}"
    return ""


def _is_container_path(norm_path: str) -> bool:
    """Return True only if this path IS a top-level install container (Program Files at depth 1).

    Depth-aware: C:/Program Files → True, but
    C:/MATS/{guid}/FileBackup/C/Program Files (x86) → False.
    """
    parts = norm_path.split("/")
    return len(parts) == 2 and parts[1] in _CONTAINER_DIR_NAMES


def _is_user_home_dir(norm_path: str) -> bool:
    """Return True if this is a user home directory (e.g. c:/users/nazar).

    Depth-aware: only matches at depth 2 (drive/users/username).
    User home dirs are profiles, not applications.
    """
    parts = norm_path.split("/")
    return len(parts) == 3 and parts[1] in _USER_CONTAINER_NAMES


def _is_drive_root(norm_path: str) -> bool:
    """Return True for drive roots such as c:/ or c:."""
    stripped = norm_path.rstrip("/")
    return bool(re.fullmatch(r"[a-z]:", stripped))


def _is_user_container_dir(norm_path: str) -> bool:
    """Return True for C:/Users-style profile containers."""
    parts = norm_path.rstrip("/").split("/")
    return len(parts) == 2 and parts[1] in _USER_CONTAINER_NAMES


def _is_appdata_packages_path(norm_path: str) -> bool:
    """Detect UWP/sandboxed app package data roots."""
    parts = norm_path.rstrip("/").split("/")
    return len(parts) >= 5 and parts[-3:] == ["appdata", "local", "packages"]


def _is_vm_storage_path(norm_path: str) -> bool:
    """Detect VM disk/config storage folders by path segment."""
    parts = set(norm_path.rstrip("/").split("/"))
    return bool(parts & _VM_STORAGE_NAMES)


def _is_development_environment_root(norm_path: str) -> bool:
    """Detect full Python/conda ecosystems that should be reviewed, not auto-cleaned."""
    parts = norm_path.rstrip("/").split("/")
    return bool(parts and parts[-1] in _DEVELOPMENT_ENV_NAMES)


def _nvidia_update_cache_root(norm_path: str) -> str:
    """Return the root NVIDIA update/cache folder that should own OTA fragments."""
    parts = norm_path.rstrip("/").split("/")
    if "programdata" not in parts or "nvidia corporation" not in parts:
        return ""
    if "updateframework" in parts:
        idx = parts.index("updateframework")
        return "/".join(parts[:idx + 1])
    if "downloader" in parts:
        idx = parts.index("downloader")
        return "/".join(parts[:idx + 1])
    if "ota-artifacts" in parts:
        idx = parts.index("ota-artifacts")
        return "/".join(parts[:idx])
    return ""


def _safety_correct_entity_type(path: str, entity_type: str) -> tuple[str, str]:
    """Apply narrow safety corrections discovered during semantic audits."""
    norm_path = path.replace("\\", "/").lower().rstrip("/")
    if _is_user_home_dir(norm_path):
        return "user_profile", "User profile root — review only"
    if _is_appdata_packages_path(norm_path):
        return "application_data", "Sandboxed application data — review only"
    if _is_vm_storage_path(norm_path):
        return "vm_storage", "Virtual machine storage — review only"
    if _is_development_environment_root(norm_path):
        return "development_environment", "Development environment — review only"
    if _nvidia_update_cache_root(norm_path):
        return "installer_cache", "NVIDIA update/cache staging — review only"
    return entity_type, ""


# Internal folder names that should be attached to parent app
_INTERNAL_DIR_NAMES = {
    "node_modules", "vendor", "packages", "plugins", "lib", "share",
    "resources", "dependencies", "deps", "libs", "ext", "extensions",
    "modules", "components", "sdk", "runtime", "bin", "include",
    "src", "source", "third_party", "3rdparty", "thirdparty",
    "data", "appdata", "app_data", "assets", "static", "public",
    "site-packages", "dist-packages", "python", "ruby", "gems",
    "packages", "nuget", "bower_components", "jspm_packages",
    "typings", "@types", "dist", "build", "out", "target",
    "debug", "release", "x64", "x86", "arm64", "win32", "win64",
    "localization", "locales", "i18n", "l10n", "lang", "languages",
    "translations", "strings", "messages",
}

# Path segments that signal "you are inside an application/framework container".
# When any ancestor of a candidate dir matches one of these, the candidate
# is suppressed in Pass 1 rather than becoming a standalone entity.
_APP_CONTAINER_SEGMENTS = {
    # Python bundled runtimes
    "_internal",
    # TeX/LaTeX trees
    "texmf", "texmf-dist", "texmf-local", "texmf-var",
    # Cross-compiler / MSVC toolchain suffixes
    "msvc_x86_64", "msvc2022_64", "msvc2019_64", "msvc2017_64",
    "mingw64", "mingw32", "ucrt64", "clang64", "clang32",
    # Framework sub-trees
    "apps",        # e.g. QGIS apps/Qt5/...
    "runtimes",    # .NET / Java runtimes bundled with apps
    "frameworks",
    "toolchain",
    "platform",
    "qtwebengine_dictionaries",
}

# _DIR_ENTITY_MAP entries that are ALWAYS meaningful regardless of depth.
# Everything else in _DIR_ENTITY_MAP requires the depth/container guards.
_ALWAYS_STANDALONE_DIR_NAMES = {
    # Dev lifecycle
    "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache",
    ".pytest_cache", ".tox", ".gradle", ".cargo", ".rustup", ".m2",
    ".ivy2", ".nuget", "bower_components",
    # GPU/shader caches
    "shadercache", "shader cache", "gpucache", "dxcache", "dxvk-cache",
    "glcache", "d3dscache", "pipelinecache", "deriveddatacache",
    # VM containers
    "virtualbox vms", "memuhyperv vms", "vmware vms",
    "virtual machines", "virtual hard disks", "bluestacks_hdd",
    # Python/conda distributions
    "miniconda3", "anaconda3", "miniforge3", "mambaforge",
    "miniconda", "anaconda", "miniforge",
    # AI tool roots
    "huggingface", "comfyui", "invokeai", "fooocus",
    "stable-diffusion", "stable-diffusion-webui",
    "text-generation-webui", "oobabooga", ".ollama",
}

# Maximum relative depth (from scan root) before generic _DIR_ENTITY_MAP
# entries are suppressed as "too deep to be a user-facing entity".
# depth 1 = direct child of scan root, depth 5 = 5 levels deep.
_MAX_GENERIC_DIR_DEPTH = 5
_MAX_TOP_LEVEL_DEPTH = 5   # max depth from scan root for installed-app / marker passes

# Human-readable labels for technical dev-artifact folder names
_DEV_ARTIFACT_LABELS: dict[str, str] = {
    "node_modules":   "npm Packages",
    "venv":           "Python Env",
    ".venv":          "Python Env",
    ".gradle":        "Gradle Cache",
    ".m2":            "Maven Cache",
    ".cargo":         "Rust/Cargo Cache",
    ".rustup":        "Rust Toolchain",
    ".tox":           "Tox Environments",
    ".nuget":         "NuGet Cache",
    "bower_components": "Bower Packages",
    "gems":           "Ruby Gems",
    "site-packages":  "Python Packages",
    "dist-packages":  "Python Packages",
    "pods":           "CocoaPods",
    ".terraform":     "Terraform Cache",
    "vendor":         "Vendor Dependencies",
    "fixtures":       "Test Fixtures",
    "fixture":        "Test Fixtures",
    "test":           "Test Assets",
    "tests":          "Test Assets",
    "__tests__":      "Test Assets",
    "spec":           "Test Assets",
    "specs":          "Test Assets",
    "mocks":          "Test Mocks",
    "__mocks__":      "Test Mocks",
    "assets":         "Development Assets",
    "static":         "Development Assets",
    "public":         "Development Assets",
    "dist":           "Build Output",
    "build":          "Build Output",
    "out":            "Build Output",
    "output":         "Build Output",
    "target":         "Build Output",
    ".next":          "Next.js Build",
    ".nuxt":          "Nuxt.js Build",
    "coverage":       "Test Coverage",
    "__snapshots__":  "Test Snapshots",
    "miniconda3":     "Miniconda",
    "anaconda3":      "Anaconda",
    "miniforge3":     "Miniforge",
    "mambaforge":     "Mambaforge",
    "miniconda":      "Miniconda",
    "anaconda":       "Anaconda",
    "miniforge":      "Miniforge",
}

_DEVELOPMENT_ENV_NAMES = {
    "miniconda3", "anaconda3", "miniforge3", "mambaforge",
    "miniconda", "anaconda", "miniforge",
}

_VM_STORAGE_NAMES = {
    "virtualbox vms", "memuhyperv vms", "vmware vms",
    "virtual machines", "virtual hard disks", "bluestacks_hdd",
    "qemu", "qemu vms", "qemu vm",
}

# Minimum size thresholds — suppress tiny artifact/cache folders that add noise
_MIN_BUILD_FOLDER_BYTES  = 5 * 1024 * 1024   # 5 MB — build outputs smaller than this are noise
_MIN_PYCACHE_BYTES       = 256 * 1024         # 256 KB — __pycache__/.mypy_cache etc.
_TINY_CACHE_NAMES        = {"__pycache__", ".mypy_cache", ".pytest_cache", "__snapshots__", "coverage"}

# Findings quality gate: these are small enough that a weakly-classified entry
# rarely gives the user a meaningful cleanup decision.
_LOW_VALUE_BYTES = 4 * 1024
_LOW_VALUE_WEAK_BYTES = 64 * 1024
_NON_ACTIONABLE_ENTITY_TYPES = {"unknown_folder", "mixed_folder", "loose_files"}
_PLACEHOLDER_DIR_NAMES = {
    "saved games", "savedgames", "fixtures", "fixture", "testdata",
    "test data", "tests data", "sample", "samples", "example", "examples",
    "placeholder", "empty", "new folder", "untitled folder",
}
_PLACEHOLDER_FILE_NAMES = {
    ".gitkeep", ".keep", ".placeholder", "placeholder", "empty",
    "readme", "readme.md", "desktop.ini", "thumbs.db",
}

_DEV_ASSET_DIR_NAMES = {
    "test", "tests", "__tests__", "spec", "specs", "fixtures", "fixture",
    "testdata", "test data", "mock", "mocks", "__mocks__", "stubs",
    "snapshots", "__snapshots__", "coverage", ".storybook", "stories",
    "assets", "static", "public",
}
_BUILD_ARTIFACT_DIR_NAMES = {
    "build", "builds", "dist", "out", "output", "outputs", "target",
    "release", "debug", ".next", ".nuxt", ".svelte-kit", ".vite",
    "cmake-build-debug", "cmake-build-release",
}
_CONFIG_DIR_NAMES = {
    "config", "configs", "configuration", "settings", ".config",
    ".vscode", ".idea", ".github", ".gitlab", ".circleci",
}
_PROJECT_MARKER_FILES = {
    "package.json", "pnpm-lock.yaml", "yarn.lock", "package-lock.json",
    "pyproject.toml", "requirements.txt", "poetry.lock", "pdm.lock",
    "cargo.toml", "cargo.lock", "go.mod", "go.sum", "pom.xml",
    "build.gradle", "settings.gradle", "composer.json", "gemfile",
    "makefile", "cmakelists.txt", "vite.config.js", "vite.config.ts",
    "next.config.js", "nuxt.config.ts", "tsconfig.json",
}
_PROJECT_MARKER_EXTS = {".sln", ".csproj", ".vcxproj", ".xcodeproj"}
_APPLICATION_SUPPORT_SEGMENTS = {
    "application support", "app support", "appdata", "programdata",
    "localappdata", "roaming", "locallow",
}

# Crash-dump folder names that need "Crash Dumps" label instead of raw folder name
_CRASH_DUMP_NAMES = {"dump", "dumps", "crashdumps", "crash", "crashes"}

# ── Known monolith distributions (Phase 1 Discovery) ─────────────
#
# Any directory whose lowercased name exactly equals a pattern, OR starts with
# a pattern, is claimed in Phase 1 before content-homogeneity analysis runs.
# This enforces The Containment Rule: no file inside a claimed root will be
# reclassified as a standalone entity by later passes.
#
# Pattern conventions:
#   "texlive"   — exact-or-prefix: matches "texlive", "texlive2025"
#   "qgis "     — trailing SPACE = strict prefix: matches "qgis 3.40.11" but not "qgisscript"
#   "r-"        — trailing DASH  = strict prefix: matches "r-4.3.0" but not "right-click"
_KNOWN_MONOLITH_PATTERNS: tuple = tuple(_RULES["monolith_patterns"])

# Human-readable display names for monolith patterns.
# When the actual folder name carries version info (e.g. "QGIS 3.40.11"),
# _monolith_display() prefers the actual name over the generic label.
_MONOLITH_DISPLAY_NAMES: dict = dict(_RULES["monolith_display_names"])

# Entity types assigned to monolith roots in Phase 1 Discovery.
# Defaults to "application" for anything not listed here.
_MONOLITH_ENTITY_TYPES: dict = dict(_RULES["monolith_entity_types"])


def _is_internal_path(path: str) -> tuple[bool, str]:
    """Check if a path is inside an internal app folder.
    
    Returns (is_internal, parent_indicator) where parent_indicator
    is the name of the folder that indicates internal status.
    """
    norm_path = path.replace("\\", "/").lower()
    parts = norm_path.split("/")
    
    for i, part in enumerate(parts):
        if part in _INTERNAL_DIR_NAMES:
            return True, part
        # node_modules with package names inside
        if part == "node_modules" and i < len(parts) - 1:
            return True, "node_modules"
        # python site-packages
        if part in ("site-packages", "dist-packages") and i < len(parts) - 1:
            return True, part
    
    return False, ""


def _get_path_depth(root: str, path: str) -> int:
    """Calculate relative depth from root to path."""
    root_norm = root.replace("\\", "/").lower().rstrip("/")
    path_norm = path.replace("\\", "/").lower().rstrip("/")
    
    if not path_norm.startswith(root_norm):
        return 0
    
    rel_path = path_norm[len(root_norm):].strip("/")
    if not rel_path:
        return 0
    
    return rel_path.count("/") + 1


def _find_owning_app(path: str, app_paths: dict[str, dict]) -> tuple[str, dict]:
    """Find the nearest owning application for a given path.
    
    Args:
        path: The path to check
        app_paths: Dict mapping normalized app paths to app info
        
    Returns:
        (app_name, app_info) or ("", {}) if no owner found
    """
    norm_path = path.replace("\\", "/").lower()
    
    best_match = ""
    best_info = {}
    best_depth = 0
    
    for app_path, app_info in app_paths.items():
        if norm_path.startswith(app_path + "/") or norm_path == app_path:
            # Calculate how deep this path is inside the app
            app_depth = app_path.count("/")
            path_depth = norm_path.count("/")
            depth_diff = path_depth - app_depth
            
            # Prefer the closest (shallowest) match
            if best_match == "" or depth_diff < best_depth:
                best_match = app_info.get("name", "")
                best_info = app_info
                best_depth = depth_diff
    
    return best_match, best_info


def _installer_display_name(filename: str) -> str:
    """Extract a clean product name from an installer filename.

    Examples:
      gstreamer-1.0-x86_64-1.22.12.msi  → gstreamer 1.0
      vlc-3.0.20-win64.exe              → vlc
      python-3.11.5-amd64.exe           → python
    """
    stem = os.path.splitext(filename)[0]
    # Strip version numbers and everything after them
    stem = re.sub(r'[-_.\s]v?\d[\d._\-].*$', '', stem, flags=re.IGNORECASE)
    # Strip trailing platform/action suffixes that may remain
    stem = re.sub(
        r'[-_.\s]?(x64|x86|amd64|arm64|win64|win32|windows|setup|install|installer)$',
        '', stem, flags=re.IGNORECASE,
    )
    stem = stem.strip('-_. ')
    return (stem or os.path.splitext(filename)[0])[:50]


def _matches_monolith(lower_name: str, extra: tuple = ()) -> bool:
    """Return True if lower_name matches any known monolith pattern (exact or prefix)."""
    for pat in _KNOWN_MONOLITH_PATTERNS + extra:
        if lower_name == pat or lower_name.startswith(pat):
            return True
    return False


def _monolith_display(lower_name: str, actual_name: str) -> str:
    """Return a display name for a matched monolith root.

    Prefers the actual folder name when it carries version information
    (e.g. actual_name="QGIS 3.40.11" is more informative than label="QGIS").
    """
    for pat, label in _MONOLITH_DISPLAY_NAMES.items():
        if lower_name == pat or lower_name.startswith(pat):
            return actual_name if len(actual_name) > len(label) else label
    return actual_name


def _monolith_type(lower_name: str) -> str:
    """Return the entity_type for a matched monolith root (defaults to 'application')."""
    for pat, etype in _MONOLITH_ENTITY_TYPES.items():
        if lower_name == pat or lower_name.startswith(pat):
            return etype
    return "application"


def _last_chance_folder_classification(
    norm_path: str,
    folder_name: str,
    direct_children: list[Finding],
) -> tuple[Optional[str], str]:
    """Classify common folder roles before falling back to Unknown."""
    lname = folder_name.lower()
    path_parts = set(norm_path.rstrip("/").split("/"))
    direct_file_names = {c.name.lower() for c in direct_children if not c.is_dir}
    direct_dir_names = {c.name.lower() for c in direct_children if c.is_dir}
    direct_exts = {c.extension.lower() for c in direct_children if not c.is_dir}

    if lname in _BUILD_ARTIFACT_DIR_NAMES:
        return "build_folder", "Build artifact folder name"

    if lname in _CACHE_KEYWORDS or lname.endswith("cache"):
        return "cache_folder", "Cache folder name"

    if lname in {"tmp", "temp", "temporary"} or lname.endswith("tmp"):
        return "temp_folder", "Temporary folder name"

    if lname in {"log", "logs"} or any(k in lname for k in ("log", "diag", "trace", "dump", "crash")):
        return "log_folder", "Log/diagnostic folder name"

    if lname in _DEV_ASSET_DIR_NAMES:
        return "dev_artifacts", "Development/test asset folder name"

    if lname in _CONFIG_DIR_NAMES:
        if path_parts & _APPLICATION_SUPPORT_SEGMENTS:
            return "application_data", "Application configuration/support data"
        return "dev_artifacts", "Configuration folder"

    if path_parts & _APPLICATION_SUPPORT_SEGMENTS:
        return "application_data", "Application support data path"

    if direct_file_names & _PROJECT_MARKER_FILES:
        return "dev_project", "Development project marker file"

    if direct_exts & _PROJECT_MARKER_EXTS:
        return "dev_project", "Development project file"

    if direct_dir_names & {".git", ".hg", ".svn", "src", "source", "tests", "test"}:
        return "dev_project", "Development project folder structure"

    if any(k in lname for k in ("project", "workspace", "repo", "repository")):
        return "dev_project", "Project/workspace folder name"

    if any(k in lname for k in ("fixture", "test", "mock", "stub", "snapshot")):
        return "dev_artifacts", "Development/test folder name"

    if any(k in lname for k in ("config", "settings", "prefs", "profile")):
        return "application_data", "Configuration/support folder name"

    if direct_exts and direct_exts.issubset(_LOG_EXTS):
        return "log_folder", "Log files"

    return None, ""


class _DetectionContext:
    """Mutable state shared across the entity-detection passes.

    detect_entities() builds one of these and threads it through each
    _pass_* function. Splitting the former ~1,200-line function this way
    lets every pass be read, reasoned about and tested on its own.
    """

    def __init__(self, findings, target_root, log_fn, progress_fn, entity_fn):
        self.findings = findings
        self.target_root = target_root
        self.root_norm = target_root.replace("\\", "/").lower().rstrip("/")
        self.log = log_fn
        self._progress_fn = progress_fn
        self.entity_fn = entity_fn

        self.log(f"[smart] detecting entities from {len(findings):,} items")
        self.log("[smart] building indexes...")
        t_index_start = _time.time()

        # Index: normalized path -> Finding; normalized parent -> children.
        self.path_index: dict[str, Finding] = {}
        self.children_index: dict[str, list[Finding]] = defaultdict(list)
        self.all_dirs: list[Finding] = []
        for f in findings:
            norm = f.path.replace("\\", "/").lower()
            parent_norm = f.parent.replace("\\", "/").lower()
            self.path_index[norm] = f
            self.children_index[parent_norm].append(f)
            if f.is_dir:
                self.all_dirs.append(f)

        self.entities: list[SmartEntity] = []
        self.claimed_paths: set[str] = set()  # normalized paths already consumed

        # Progress / coverage counters.
        self.total_candidates = len(self.all_dirs)
        self.total_files = len([f for f in findings if not f.is_dir])
        self.processed_candidates = 0
        self.entities_created = 0
        self.grouped_file_count = 0  # incremental — avoids O(n) sum each call

        # Detection confidence of the pass currently running (0.0-1.0).
        # emit_entity() stamps it onto each entity that has none of its own.
        self.confidence = 0.5

        # Installed-program registry — used by passes 1, 2 and 2b.
        self.installed_apps = _get_installed_programs()
        if self.installed_apps:
            self.log(f"[smart] loaded {len(self.installed_apps):,} installed "
                     f"programs from registry")
        self.detected_app_paths: dict[str, dict] = {}  # norm_path -> app_info

        self.log(f"[smart] entity detection started · candidate folders: "
                 f"{self.total_candidates:,}")
        elapsed = _time.time() - t_index_start
        if elapsed > 0.05:
            self.log(f"[perf] index build: {elapsed * 1000:.1f}ms")

    # ── progress / emission ────────────────────────────────────────
    def coverage_progress(self, phase: str):
        """Emit a progress update with live semantic coverage stats.

        Coverage % is entity-based (entities_created / total_candidates) so
        the bar moves smoothly even when claimed_paths grows slower.
        """
        ungrouped_f = max(0, self.total_files - self.grouped_file_count)
        pct = int(self.entities_created / max(self.total_candidates, 1) * 100)
        if phase != "complete":
            pct = min(pct, 99)  # cap at 99 until "complete" fires the final 100
        self._progress_fn(phase, self.grouped_file_count, ungrouped_f,
                          self.entities_created, pct)

    def emit_entity(self, ent: SmartEntity, claimed_file_count: int = 0):
        """Record a detected entity and update running counters.

        claimed_file_count: files actually claimed by claim() for this entity
        (may differ from ent.file_count when built from direct children).
        """
        self.entities.append(ent)
        if not ent.confidence_score:
            ent.confidence_score = self.confidence
        self.entities_created += 1
        self.grouped_file_count += (
            claimed_file_count if claimed_file_count > 0 else ent.file_count
        )
        self.entity_fn(ent)  # stream to AI if a callback was provided
        if self.entities_created % 10 == 0:  # throttle UI updates
            self.coverage_progress("grouping")

    # ── tree traversal over the prefix index ───────────────────────
    def gather(self, dir_norm: str, limit: int = 1000) -> list[Finding]:
        """Gather up to `limit` descendants under dir_norm."""
        result: list[Finding] = []
        stack = [dir_norm]
        while stack and len(result) < limit:
            d = stack.pop()
            for child in self.children_index.get(d, []):
                if len(result) >= limit:
                    break
                result.append(child)
                if child.is_dir:
                    stack.append(child.path.replace("\\", "/").lower())
        return result

    def gather_direct(self, dir_norm: str) -> list[Finding]:
        """Return only the direct children of dir_norm (depth=1, no recursion)."""
        return list(self.children_index.get(dir_norm, []))

    def claim(self, dir_norm: str) -> int:
        """Mark a directory and all descendants as claimed.

        Returns the number of *files* (non-dirs) newly claimed.
        """
        newly_claimed_files = 0
        self.claimed_paths.add(dir_norm)
        stack = [dir_norm]
        while stack:
            d = stack.pop()
            for child in self.children_index.get(d, []):
                c_norm = child.path.replace("\\", "/").lower()
                if c_norm not in self.claimed_paths:
                    self.claimed_paths.add(c_norm)
                    if child.is_dir:
                        stack.append(c_norm)
                    else:
                        newly_claimed_files += 1
        return newly_claimed_files


def _phase1_discovery(ctx: "_DetectionContext", extra_pats: tuple):
    """PHASE 1 - DISCOVERY (Container-First Containment Rule).

    Walk shallow dirs and claim every known monolith root BEFORE content
    analysis runs. The Containment Rule: any file inside a claimed root
    belongs to that root, full stop -- no later reclassification.
    """
    ctx.log("[smart] phase 1: discovery — scanning for known monolith "
            "distributions...")
    ctx.confidence = 0.9  # matched a known distribution name
    t_p1_start = _time.time()
    p1_roots_found = 0

    # Only scan shallow dirs (root_depth + 4 levels max) -- Phase 1 must be fast.
    _p1_depth_limit = ctx.root_norm.count("/") + 4
    p1_dirs = sorted(
        (
            d for d in ctx.all_dirs
            if d.path.replace("\\", "/").lower().count("/") <= _p1_depth_limit
        ),
        key=lambda d: d.path.replace("\\", "/").count("/"),
    )

    for _f in p1_dirs:
        _norm = _f.path.replace("\\", "/").lower()
        if _norm in ctx.claimed_paths:
            continue
        if _norm == ctx.root_norm or _is_user_home_dir(_norm):
            continue
        _lower = _f.name.lower()
        if not _matches_monolith(_lower, extra_pats):
            continue

        _display = _monolith_display(_lower, _f.name)
        _etype = _monolith_type(_lower)
        _children = ctx.gather(_norm)
        _ent = _build_entity(
            _f.path, _display, _etype, _children,
            f"Known monolith distribution: {_f.name}",
        )
        _fc = ctx.claim(_norm)
        ctx.emit_entity(_ent, _fc)
        p1_roots_found += 1

    t_p1_elapsed = int((_time.time() - t_p1_start) * 1000)
    ctx.log(f"[smart] phase 1: discovery — found {p1_roots_found} entity "
            f"roots · {t_p1_elapsed}ms")


def _pass0_update_caches(ctx: "_DetectionContext"):
    """Pass 0 - merge vendor update caches that otherwise fragment into
    pseudo-apps (NVIDIA update/cache staging)."""
    ctx.log("[smart] pass 0: merging known update cache fragments...")
    ctx.confidence = 0.85  # NVIDIA update-cache staging
    pass_entities = 0
    for f in sorted(ctx.all_dirs,
                    key=lambda d: d.path.replace("\\", "/").count("/")):
        norm_path = f.path.replace("\\", "/").lower().rstrip("/")
        if norm_path in ctx.claimed_paths:
            continue
        cache_root = _nvidia_update_cache_root(norm_path)
        if not cache_root or cache_root != norm_path:
            continue

        children = ctx.gather(norm_path)
        if not children:
            continue
        ent = _build_entity(
            f.path, "NVIDIA Update Cache", "installer_cache", children,
            "NVIDIA update/cache staging folder",
        )
        fc = ctx.claim(norm_path)
        ctx.emit_entity(ent, fc)
        pass_entities += 1
    if pass_entities:
        ctx.log(f"[smart]   → merged {pass_entities} update cache entities")


def _pass1_known_dirs(ctx: "_DetectionContext"):
    """Pass 1 - known directory names (node_modules, venv, cache, .git, ...).

    Process shallowest dirs first so parent dirs claim their children
    before the children can become standalone entities.
    """
    ctx.log("[smart] pass 1: detecting known directories "
            "(node_modules, venv, cache, etc.)...")
    ctx.confidence = 0.85  # known directory name
    pass_entities = 0
    root_depth = ctx.root_norm.count("/")
    pass1_dirs = sorted(ctx.all_dirs,
                        key=lambda d: d.path.replace("\\", "/").count("/"))

    for f in pass1_dirs:
        ctx.processed_candidates += 1
        lower_name = f.name.lower()
        norm_path = f.path.replace("\\", "/").lower()

        if norm_path in ctx.claimed_paths:
            continue
        if lower_name not in _DIR_ENTITY_MAP:
            continue

        etype = _DIR_ENTITY_MAP[lower_name]
        corrected_type, corrected_reason = _safety_correct_entity_type(f.path, etype)
        if corrected_type != etype:
            etype = corrected_type

        # ── Ownership / depth guards ───────────────────────────────
        # Always-standalone entries (dev tools, GPU caches, VM containers,
        # AI roots) are created regardless of depth; only the registry
        # guard applies.
        if lower_name in _ALWAYS_STANDALONE_DIR_NAMES:
            if norm_path not in ctx.installed_apps and _is_installed_app(
                    f.path, ctx.installed_apps):
                continue
        else:
            # Guard 1: inside a registry-registered installed app
            if norm_path not in ctx.installed_apps and _is_installed_app(
                    f.path, ctx.installed_apps):
                continue
            # Guard 2: inside Program Files deeper than PF/<app>/<direct-child>
            if _is_program_files_path(norm_path) and len(norm_path.split("/")) > 4:
                continue
            # Guard 3: any ancestor segment is a framework/runtime container
            if any(part in _APP_CONTAINER_SEGMENTS
                   for part in norm_path.split("/")):
                continue
            # Guard 4: too many levels from scan root for a generic entity
            rel_depth = norm_path.count("/") - root_depth
            if rel_depth > _MAX_GENERIC_DIR_DEPTH:
                continue

        # For VCS dirs (.git, .svn, .hg), the entity is the parent project.
        if lower_name in (".git", ".svn", ".hg"):
            parent = f.parent
            parent_norm = parent.replace("\\", "/").lower()
            if parent_norm in ctx.claimed_paths:
                continue
            children = ctx.gather(parent_norm)
            ent = _build_entity(parent, os.path.basename(parent),
                                "dev_project", children,
                                f"Contains {lower_name} directory")
            ctx.claim(parent_norm)
            ctx.emit_entity(ent)
            pass_entities += 1
            continue

        children = ctx.gather(norm_path)

        # ── Size filter: suppress tiny build outputs and pycaches ──────
        if etype == "build_folder":
            total_sz = sum(c.size_bytes for c in children)
            if total_sz < _MIN_BUILD_FOLDER_BYTES:
                fc = ctx.claim(norm_path)
                ctx.grouped_file_count += fc
                continue
        elif etype == "cache_folder" and lower_name in _TINY_CACHE_NAMES:
            total_sz = sum(c.size_bytes for c in children)
            if total_sz < _MIN_PYCACHE_BYTES:
                fc = ctx.claim(norm_path)
                ctx.grouped_file_count += fc
                continue

        # ── Display name with ownership context ───────────────────────
        if etype in ("build_folder", "dev_artifacts", "development_environment",
                     "venv", "node_modules"):
            label = _DEV_ARTIFACT_LABELS.get(lower_name, f.name)
            parent_name = os.path.basename(f.parent)
            parent_lower = parent_name.lower()
            if etype == "development_environment":
                display = label
            elif (parent_name
                    and parent_lower not in _QUALIFIER_SKIP_SEGS
                    and parent_lower not in _GENERIC_FOLDER_NAMES):
                display = f"{label} – {parent_name}"
            else:
                display = label
        elif etype == "application_data":
            display = "Application Packages"
        elif etype == "vm_storage":
            display = _qualify_folder_name(f.name, f.path)
        elif etype == "installer_cache":
            display = "NVIDIA Update Cache"
        elif etype == "installer":
            source = _infer_cache_source(norm_path)
            display = (f"{source} Installer" if source
                       else _qualify_folder_name(f.name, f.path))
        elif etype == "log_folder":
            source = _infer_cache_source(norm_path)
            if lower_name in _CRASH_DUMP_NAMES:
                display = f"Crash Dumps – {source}" if source else "Crash Dumps"
            else:
                display = (f"Logs – {source}" if source
                           else _qualify_folder_name(f.name, f.path))
        else:
            display = _qualify_folder_name(f.name, f.path)

        ent = _build_entity(
            f.path, display, etype, children,
            corrected_reason or f"Known directory: {f.name}",
        )
        ctx.claim(norm_path)
        ctx.emit_entity(ent)
        pass_entities += 1

    ctx.coverage_progress("known_dirs")
    ctx.log(f"[smart]   → created {pass_entities} known-dir entities "
            f"· total: {ctx.entities_created}")


def _pass2_installed_apps(ctx: "_DetectionContext"):
    """Pass 2 - installed applications detected via the Windows registry."""
    ctx.log("[smart] pass 2: detecting installed applications from registry...")
    ctx.confidence = 1.0  # verified against the Windows registry
    pass_entities = 0
    installed_entities = 0
    skipped_internal = 0

    for f in ctx.all_dirs:
        ctx.processed_candidates += 1
        norm_path = f.path.replace("\\", "/").lower()
        if norm_path in ctx.claimed_paths:
            continue

        # Never treat a user home directory (c:/users/<name>) as an application.
        if _is_user_home_dir(norm_path):
            continue

        # Skip if too deep (internal component).
        depth = _get_path_depth(ctx.target_root, f.path)
        if depth > _MAX_TOP_LEVEL_DEPTH:
            is_internal, indicator = _is_internal_path(f.path)
            if is_internal:
                skipped_internal += 1
                continue

        app_info = _is_installed_app(f.path, ctx.installed_apps)
        if app_info:
            # ── Program Files consolidation ───────────────────────────
            # When a registry entry points deep inside Program Files,
            # create the entity at the VENDOR level so sub-installations
            # merge into one entity instead of separate fragments.
            entity_path = f.path
            entity_norm = norm_path
            entity_name = app_info["name"]

            if _is_program_files_path(norm_path):
                parts = norm_path.split("/")
                if len(parts) > 4:  # deeper than drive/pfiles/vendor/app
                    vendor_norm = "/".join(parts[:3])
                    if vendor_norm in ctx.claimed_paths:
                        continue  # already created one entity here
                    vendor_finding = ctx.path_index.get(vendor_norm)
                    if vendor_finding:
                        entity_path = vendor_finding.path
                        entity_norm = vendor_norm
                        entity_name = vendor_finding.name

            if entity_norm in ctx.claimed_paths:
                continue

            children = ctx.gather(entity_norm)
            ent = _build_entity(
                entity_path, entity_name, "application", children,
                f"Installed application from registry: "
                f"{app_info.get('publisher', 'Unknown')}",
                app_version=app_info.get("version", ""),
                app_publisher=app_info.get("publisher", ""),
                install_date=app_info.get("install_date", ""),
            )
            ctx.claim(entity_norm)
            ctx.emit_entity(ent)
            ctx.detected_app_paths[entity_norm] = app_info
            pass_entities += 1
            installed_entities += 1

    if installed_entities > 0:
        ctx.log(f"[smart]   → detected {installed_entities} installed "
                f"applications from registry")
    if skipped_internal > 0:
        ctx.log(f"[smart]   → skipped {skipped_internal} deeply nested "
                f"internal folders")


def _pass2b_app_markers(ctx: "_DetectionContext"):
    """Pass 2b - portable applications and detached databases, found by
    marker files (exe names, .sqlite/.db extensions)."""
    ctx.log("[smart] pass 2b: detecting portable applications by marker files...")
    ctx.confidence = 0.85  # application marker file
    pass_entities_portable = 0
    files_processed = 0

    for f in ctx.findings:
        files_processed += 1
        if f.is_dir:
            continue
        lower_name = f.name.lower()

        if lower_name in _APP_MARKERS:
            parent = f.parent
            parent_norm = parent.replace("\\", "/").lower()

            nvidia_cache_norm = _nvidia_update_cache_root(parent_norm)
            if nvidia_cache_norm:
                if nvidia_cache_norm in ctx.claimed_paths:
                    continue
                nvidia_finding = ctx.path_index.get(nvidia_cache_norm)
                if nvidia_finding:
                    parent = nvidia_finding.path
                    parent_norm = nvidia_cache_norm

            # ── Grandparent promotion ─────────────────────────────────
            # If the exe lives in a generic subdir (bin/, app/, ...), use
            # the grandparent as the entity root so C:/ffmpeg/bin/ffmpeg.exe
            # gives entity root C:/ffmpeg.
            _GENERIC_SUBDIRS = {"bin", "app", "src", "lib", "core",
                                "x64", "x86", "win32", "win64", "arm64",
                                "debug", "release", "out", "obj"}
            parent_leaf = os.path.basename(parent).lower()
            if parent_leaf in _GENERIC_SUBDIRS:
                grandparent = os.path.dirname(parent)
                grandparent_norm = grandparent.replace("\\", "/").lower()
                if (grandparent_norm and grandparent_norm not in ctx.claimed_paths
                        and grandparent_norm != ctx.root_norm
                        and not _is_user_home_dir(grandparent_norm)):
                    parent = grandparent
                    parent_norm = grandparent_norm

            if parent_norm in ctx.claimed_paths:
                continue

            if _is_user_home_dir(parent_norm) or parent_norm.rstrip("/") == ctx.root_norm:
                continue

            # Skip if too deep (internal component).
            depth = _get_path_depth(ctx.target_root, parent)
            if depth > _MAX_TOP_LEVEL_DEPTH:
                is_internal, indicator = _is_internal_path(parent)
                if is_internal:
                    continue

            marker_type, display_name = _APP_MARKERS[lower_name]
            if display_name is None:
                display_name = os.path.basename(parent)
            if nvidia_cache_norm:
                display_name = "NVIDIA Update Cache"

            # If already in the registry it's an installed app, not portable.
            if _is_installed_app(parent, ctx.installed_apps):
                continue

            children = ctx.gather(parent_norm)
            if nvidia_cache_norm:
                entity_type = "installer_cache"
                marker_reason = "NVIDIA update/cache staging folder"
            elif marker_type == "application":
                entity_type = "portable_app"
                marker_reason = f"Portable application marker: {f.name}"
            else:
                entity_type = marker_type
                marker_reason = f"Project/application marker: {f.name}"

            ent = _build_entity(parent, display_name, entity_type, children,
                                marker_reason)
            fc = ctx.claim(parent_norm)
            ctx.emit_entity(ent, fc)
            if entity_type in ("application", "portable_app", "installer_cache"):
                ctx.detected_app_paths[parent_norm] = {"name": display_name, "path": parent}
            pass_entities_portable += 1
            continue

        # Extension-based markers (databases).
        ext_lower = f.extension.lower() if f.extension else ""
        if ext_lower in _APP_MARKERS and f.size_bytes >= 1024 * 1024:  # 1 MB min
            parent = f.parent
            parent_norm = parent.replace("\\", "/").lower()
            norm_file = f.path.replace("\\", "/").lower()
            if parent_norm in ctx.claimed_paths:
                continue

            # Skip databases internal to an already-detected application.
            owner_app, owner_info = _find_owning_app(f.path, ctx.detected_app_paths)
            if owner_app:
                continue

            # Skip databases inside Program Files - internal app resources.
            if _is_program_files_path(norm_file):
                continue

            etype, display_name = _APP_MARKERS[ext_lower]

            # Try to associate with a known installed application.
            related_hint = _find_related_app(f.path, f.name, ctx.installed_apps)
            if related_hint:
                display_name = related_hint
            elif display_name is None:
                display_name = f"{f.name} ({os.path.basename(parent)})"

            children = ctx.gather(parent_norm)
            ent = _build_entity(parent, display_name, etype, children,
                                f"Database file: {f.name}")
            ctx.claim(parent_norm)
            ctx.emit_entity(ent)
            pass_entities_portable += 1

    ctx.log(f"[smart]   → scanned {files_processed:,} files · created "
            f"{pass_entities_portable} portable app entities · total "
            f"entities: {ctx.entities_created}")


def _pass3_browser_profiles(ctx: "_DetectionContext"):
    """Pass 3 - browser profile/data folders, by path keyword + context."""
    ctx.log("[smart] pass 3: detecting browser profiles...")
    ctx.confidence = 0.8  # browser keyword + profile-storage context
    pass_entities = 0
    for f in ctx.all_dirs:
        ctx.processed_candidates += 1
        norm_path = f.path.replace("\\", "/").lower()
        if norm_path in ctx.claimed_paths:
            continue

        lower_name = f.name.lower()
        # Require path context to avoid false positives: a folder named
        # "chrome" in a CSS project must not become a browser profile.
        _BROWSER_CONTEXT_HINTS = {"appdata", "user data", "application support",
                                  "roaming", "localappdata", "profiles", "mozilla"}
        path_has_browser_context = any(h in norm_path for h in _BROWSER_CONTEXT_HINTS)

        if path_has_browser_context and (
            lower_name in _BROWSER_KEYWORDS
            or any(bk in norm_path for bk in _BROWSER_KEYWORDS)
        ):
            children = ctx.gather(norm_path)
            if children:
                browser_label = lower_name.title() if lower_name in _BROWSER_KEYWORDS else next(
                    (k.title() for k in _BROWSER_KEYWORDS if k in norm_path),
                    lower_name.title(),
                )
                ent = _build_entity(f.path, f"{browser_label} Data",
                                    "browser_profile", children,
                                    f"Browser profile path: {lower_name}")
                ctx.claim(norm_path)
                ctx.emit_entity(ent)
                pass_entities += 1

    ctx.coverage_progress("browser_profiles")
    ctx.log(f"[smart]   → created {pass_entities} browser profile "
            f"entities · total: {ctx.entities_created}")


def _pass3b_games(ctx: "_DetectionContext"):
    """Pass 3b - installed games, identified by platform library structure
    (Steam/Epic/GOG/Ubisoft) rather than path substring matching."""
    ctx.log("[smart] pass 3b: detecting installed games from platforms...")
    ctx.confidence = 0.9  # game platform library structure
    pass_entities = 0
    _game_lib_containers: set[str] = set()  # claimed silently after games built

    _EPIC_NON_GAME = {"launcher", "portal", "prerequisites", "directxredist",
                      "vcredist", "engine", "unreal engine"}

    for f in ctx.all_dirs:
        ctx.processed_candidates += 1
        norm_path = f.path.replace("\\", "/").lower()
        if norm_path in ctx.claimed_paths:
            continue

        parent_norm = f.parent.replace("\\", "/").lower()
        parent_name = os.path.basename(parent_norm)
        grandparent_name = os.path.basename(os.path.dirname(parent_norm))
        lower_name = f.name.lower()
        platform_label = None

        # Steam: direct child of "common" whose parent is "steamapps".
        if parent_name == "common" and grandparent_name == "steamapps":
            platform_label = "Steam"
            _game_lib_containers.add(parent_norm)
            _game_lib_containers.add(os.path.dirname(parent_norm))
        # Epic: direct child of a dir named "epic games".
        elif parent_name == "epic games" and lower_name not in _EPIC_NON_GAME:
            platform_label = "Epic Games"
            _game_lib_containers.add(parent_norm)
        # GOG: direct child of a dir named "gog games".
        elif parent_name == "gog games":
            platform_label = "GOG"
            _game_lib_containers.add(parent_norm)
        # Ubisoft Connect: children of its "games" sub-dir.
        elif parent_name == "games" and "ubisoft" in parent_norm:
            platform_label = "Ubisoft"
            _game_lib_containers.add(parent_norm)

        if platform_label:
            children = ctx.gather(norm_path)
            if children:
                ent = _build_entity(f.path, f.name, "game", children,
                                    f"{platform_label} game installation")
                fc = ctx.claim(norm_path)
                ctx.emit_entity(ent, fc)
                pass_entities += 1

    # Claim library containers without creating entities for them.
    for container_norm in _game_lib_containers:
        if container_norm not in ctx.claimed_paths:
            ctx.claim(container_norm)

    if pass_entities > 0:
        ctx.log(f"[smart]   → detected {pass_entities} installed games")


def _pass4_cache_folders(ctx: "_DetectionContext"):
    """Pass 4 - cache/temp folders by name keyword."""
    ctx.log("[smart] pass 4: detecting cache and temp folders...")
    ctx.confidence = 0.75  # cache/temp name keyword
    pass_entities = 0
    for f in ctx.all_dirs:
        ctx.processed_candidates += 1
        norm_path = f.path.replace("\\", "/").lower()
        if norm_path in ctx.claimed_paths:
            continue

        lower_name = f.name.lower()
        if (lower_name in _CACHE_KEYWORDS or lower_name.endswith("cache")
                or lower_name.endswith("tmp")):
            children = ctx.gather(norm_path)
            source_app = _infer_cache_source(norm_path)
            display_name = f"Cache for {source_app}" if source_app else f.name
            ent = _build_entity(
                f.path, display_name, "cache_folder", children,
                "Cache folder" + (f" for {source_app}" if source_app else ""),
            )
            ctx.claim(norm_path)
            ctx.emit_entity(ent)
            pass_entities += 1

    ctx.coverage_progress("cache_folders")
    ctx.log(f"[smart]   → created {pass_entities} cache/temp entities "
            f"· total: {ctx.entities_created}")


def _pass5_protected(ctx: "_DetectionContext"):
    """Pass 5 - protected / system paths, and Program Files containers."""
    ctx.log("[smart] pass 5: detecting protected system paths...")
    ctx.confidence = 0.9  # system / protected path
    pass_entities = 0
    skipped_containers = 0
    for f in ctx.all_dirs:
        ctx.processed_candidates += 1
        norm_path = f.path.replace("\\", "/").lower()
        if norm_path in ctx.claimed_paths:
            continue

        if _is_protected_path(norm_path):
            children = ctx.gather(norm_path)
            ent = _build_entity(f.path, f.name, "protected_system", children,
                                "System or protected path detected")
            fc = ctx.claim(norm_path)
            ctx.emit_entity(ent, fc)
            pass_entities += 1
        elif _is_container_path(norm_path):
            # Container paths (Program Files) should NOT become System
            # entities if they contain meaningful child entities.
            children = ctx.gather(norm_path)
            claimed_children = [
                c for c in children
                if c.path.replace("\\", "/").lower() in ctx.claimed_paths
            ]

            if claimed_children:
                ctx.log(f"[smart] skipped parent container entity: {f.name} "
                        f"· {len(claimed_children)} child entities already "
                        f"detected")
                skipped_containers += 1
                fc = ctx.claim(norm_path)
                ctx.grouped_file_count += fc
            else:
                # No meaningful children -- items inside Program Files are
                # installed applications, not generic unknown content.
                if children:
                    ent = _build_entity(f.path, f.name, "portable_app", children,
                                        "Installed application (Program Files)")
                    fc = ctx.claim(norm_path)
                    ctx.emit_entity(ent, fc)
                    pass_entities += 1

    ctx.coverage_progress("protected_paths")
    ctx.log(f"[smart]   → created {pass_entities} protected/system "
            f"entities · skipped {skipped_containers} containers "
            f"· total: {ctx.entities_created}")


def _pass6_content_folders(ctx: "_DetectionContext"):
    """Pass 6 - content-homogeneous folders (photos/video/audio/docs/...).

    Only top-level unclaimed dirs are processed; subdirectories are covered
    transitively when their parent is claimed.
    """
    ctx.log("[smart] pass 6: building media and content hierarchies...")
    ctx.confidence = 0.6  # extension-distribution heuristic
    pass_entities = 0
    last_progress_log = 0
    unclaimed_dirs = [
        f for f in ctx.all_dirs
        if f.path.replace("\\", "/").lower() not in ctx.claimed_paths
        and (
            f.parent.replace("\\", "/").lower() in ctx.claimed_paths
            or f.parent.replace("\\", "/").lower().rstrip("/") == ctx.root_norm
        )
    ]
    unclaimed_dirs.sort(key=lambda d: d.path.count("/") + d.path.count("\\"),
                        reverse=True)

    for d in unclaimed_dirs:
        norm_path = d.path.replace("\\", "/").lower()
        if norm_path in ctx.claimed_paths:
            continue

        direct = ctx.gather_direct(norm_path)
        direct_files = [c for c in direct if not c.is_dir]
        if not direct_files:
            continue

        etype = _classify_by_content(direct_files)
        if etype:
            display = _qualify_folder_name(d.name, d.path)
            ent = _build_entity(d.path, display, etype, direct, "Content analysis")
            fc = ctx.claim(norm_path)
            ctx.emit_entity(ent, fc)
            pass_entities += 1
            if pass_entities - last_progress_log >= 50:
                ctx.log(f"[smart]   → grouped {pass_entities} content "
                        f"entities so far... (total: {ctx.entities_created})")
                last_progress_log = pass_entities
                ctx.coverage_progress("content_grouping")

    ctx.log(f"[smart]   → created {pass_entities} content entities "
            f"· total: {ctx.entities_created}")


def _pass7_sweep(ctx: "_DetectionContext"):
    """Pass 7 - sweep remaining top-level unclaimed dirs into name-based
    or unknown_folder entities, so nothing is left ungrouped."""
    ctx.log("[smart] pass 7: sweeping remaining unclaimed top-level folders...")
    pass_entities = 0

    unclaimed_dirs = [
        f for f in ctx.all_dirs
        if f.path.replace("\\", "/").lower() not in ctx.claimed_paths
        and (
            f.parent.replace("\\", "/").lower() in ctx.claimed_paths
            or f.parent.replace("\\", "/").lower().rstrip("/") == ctx.root_norm
        )
    ]

    _pass7_total = len(unclaimed_dirs)
    ctx.log(f"[smart]   sweeping {_pass7_total} top-level unclaimed dirs...")
    for _pass7_i, d in enumerate(unclaimed_dirs):
        norm_path = d.path.replace("\\", "/").lower()
        if norm_path in ctx.claimed_paths:
            continue
        if norm_path.rstrip("/") == ctx.root_norm or _is_drive_root(norm_path):
            ctx.claimed_paths.add(norm_path.rstrip("/"))
            continue

        direct = ctx.gather_direct(norm_path)
        direct_files = [c for c in direct if not c.is_dir]
        descendants = ctx.gather(norm_path)
        claimed_descendants = [
            c for c in descendants
            if c.path.replace("\\", "/").lower() in ctx.claimed_paths
        ]
        unclaimed_descendant_files = [
            c for c in descendants
            if not c.is_dir and c.path.replace("\\", "/").lower() not in ctx.claimed_paths
        ]
        if claimed_descendants and not unclaimed_descendant_files:
            ctx.claim(norm_path)
            continue

        etype = _classify_by_content(direct_files) if direct_files else None

        # ── Name-based fallback before unknown_folder ──────────────────
        lower_name = d.name.lower()
        fallback_reason = ""
        if _is_user_home_dir(norm_path.rstrip("/")):
            etype = "user_profile"
        elif _is_appdata_packages_path(norm_path):
            etype = "application_data"
        elif _is_vm_storage_path(norm_path):
            etype = "vm_storage"
        elif _is_development_environment_root(norm_path):
            etype = "development_environment"
        elif _nvidia_update_cache_root(norm_path):
            etype = "installer_cache"
        elif not etype:
            fallback_type, fallback_reason = _last_chance_folder_classification(
                norm_path, d.name, direct
            )
            if fallback_type:
                etype = fallback_type
            elif lower_name in _DIR_ENTITY_MAP:
                etype = _DIR_ENTITY_MAP[lower_name]
                corrected_type, _ = _safety_correct_entity_type(d.path, etype)
                etype = corrected_type
            else:
                # Substring keyword fallback for compound names.
                lname = lower_name
                if any(k in lname for k in ("cache", "caches", "tmp", "temp")):
                    etype = "cache_folder"
                elif any(k in lname for k in ("log", "logs", "diag", "trace",
                                              "dump", "crash", "error")):
                    etype = "log_folder"
                elif any(k in lname for k in ("backup", "backups", "bak", "old",
                                              "archive")):
                    etype = "backup_group"
                elif any(k in lname for k in ("install", "setup", "update",
                                              "patch", "deploy")):
                    etype = "installer"
                elif any(k in lname for k in ("export", "exports", "output",
                                              "report")):
                    etype = "backup_group"
                elif any(k in lname for k in ("photo", "image", "picture",
                                              "screenshot")):
                    etype = "photo_collection"
                elif any(k in lname for k in ("video", "movie", "film", "clip")):
                    etype = "video_collection"
                elif any(k in lname for k in ("music", "audio", "sound", "song")):
                    etype = "audio_collection"
                elif any(k in lname for k in ("doc", "document", "manual",
                                              "guide", "readme")):
                    etype = "document_folder"
                else:
                    etype = "unknown_folder"

        corrected_type, corrected_reason = _safety_correct_entity_type(d.path, etype)
        if corrected_type != etype:
            etype = corrected_type

        reason = corrected_reason or fallback_reason or (
            "Name-based classification" if etype != "unknown_folder"
            else "Ungrouped folder"
        )

        # ── Display name with ownership context ───────────────────────
        if etype == "user_profile":
            display = f"{d.name} Profile"
        elif etype == "application_data":
            display = "Application Packages"
        elif etype in ("build_folder", "dev_artifacts", "development_environment"):
            display = _DEV_ARTIFACT_LABELS.get(lower_name, d.name)
        elif etype == "installer_cache":
            display = "NVIDIA Update Cache"
        elif etype == "installer":
            source = _infer_cache_source(norm_path)
            display = (f"{source} Installer" if source
                       else _qualify_folder_name(d.name, d.path))
        elif etype == "log_folder":
            source = _infer_cache_source(norm_path)
            if any(k in lower_name for k in _CRASH_DUMP_NAMES):
                display = f"Crash Dumps – {source}" if source else "Crash Dumps"
            else:
                display = (f"Logs – {source}" if source
                           else _qualify_folder_name(d.name, d.path))
        else:
            display = _qualify_folder_name(d.name, d.path)

        # Name-classified folders are a weak signal; a bare unknown_folder
        # is weaker still.
        ctx.confidence = 0.4 if etype != "unknown_folder" else 0.2
        ent = _build_entity(d.path, display, etype, descendants, reason)
        fc = ctx.claim(norm_path)
        ctx.emit_entity(ent, fc)
        pass_entities += 1

        if _pass7_i % 25 == 0:
            ctx.coverage_progress("unknown_sweep")

    ctx.coverage_progress("unknown_sweep")
    ctx.log(f"[smart]   → created {pass_entities} unknown/mixed folder "
            f"entities · total: {ctx.entities_created}")


def _pass8_loose_files(ctx: "_DetectionContext"):
    """Pass 8 - bucket every still-unclaimed file by type, so no loose
    file remains invisible in Findings."""
    ctx.log("[smart] pass 8: bucketing remaining loose files by type...")
    loose_files = [
        f for f in ctx.findings
        if not f.is_dir
        and f.path.replace("\\", "/").lower() not in ctx.claimed_paths
    ]
    if not loose_files:
        return

    _buckets: dict[str, list[Finding]] = defaultdict(list)
    for f in loose_files:
        ext = f.extension.lower() if f.extension else ""
        if ext in _IMAGE_EXTS or ext in _VIDEO_EXTS or ext in _AUDIO_EXTS:
            _buckets["media_collection"].append(f)
        elif ext in _DOC_EXTS:
            _buckets["document_folder"].append(f)
        elif ext in _ARCHIVE_EXTS or ext in _INSTALLER_EXTS:
            _buckets["archive_group"].append(f)
        elif ext in _DATABASE_EXTS:
            _buckets["database"].append(f)
        elif ext in _MODEL_EXTS:
            _buckets["ai_models"].append(f)
        elif ext in _LOG_EXTS or ext in _BACKUP_EXTS:
            _buckets["log_folder"].append(f)
        else:
            _buckets["mixed_folder"].append(f)

    _BUCKET_LABELS = {
        "media_collection": "Loose media files",
        "document_folder":  "Loose documents",
        "archive_group":    "Loose archives",
        "database":         "Loose database files",
        "ai_models":        "Loose AI model files",
        "log_folder":       "Loose logs and backups",
        "mixed_folder":     "Misc files",
    }

    def _dir_display_name(dir_path: str) -> str:
        """Display-friendly dir name, handling drive roots like C:/."""
        stripped = dir_path.rstrip("/\\")
        name = os.path.basename(stripped)
        if not name:
            name = stripped or "root"
        return name

    pass8_entities = 0
    for bucket_type, bucket_files in _buckets.items():
        if not bucket_files:
            continue
        # Typed buckets are a moderate signal; the misc catch-all is weak.
        ctx.confidence = 0.25 if bucket_type == "mixed_folder" else 0.5

        if bucket_type == "archive_group":
            # Installer files get individual named entities; remaining
            # archives stay grouped in one entity.
            inst_files = [f for f in bucket_files
                          if f.extension.lower() in _INSTALLER_EXTS]
            arch_files = [f for f in bucket_files
                          if f.extension.lower() not in _INSTALLER_EXTS]

            for inst in inst_files:
                product = _installer_display_name(inst.name)
                ent = SmartEntity(
                    path=inst.parent,
                    name=f"Installer ({product})",
                    entity_type="installer",
                    size_bytes=inst.size_bytes,
                    file_count=1,
                    folder_count=0,
                    modified=inst.modified,
                    accessed=inst.accessed,
                    children_sample=[inst.name],
                )
                ctx.emit_entity(ent)
                ctx.claimed_paths.add(inst.path.replace("\\", "/").lower())
                pass8_entities += 1

            if arch_files:
                total_sz = sum(f.size_bytes for f in arch_files)
                ent = SmartEntity(
                    path=ctx.target_root,
                    name=_BUCKET_LABELS["archive_group"],
                    entity_type="archive_group",
                    size_bytes=total_sz,
                    file_count=len(arch_files),
                    folder_count=0,
                    modified=max((f.modified for f in arch_files), default=0),
                    accessed=max((f.accessed for f in arch_files), default=0),
                    children_sample=[f.name for f in arch_files[:15]],
                )
                ctx.emit_entity(ent)
                for f in arch_files:
                    ctx.claimed_paths.add(f.path.replace("\\", "/").lower())
                pass8_entities += 1

        elif bucket_type == "mixed_folder":
            # Always split misc files by parent directory so the location
            # is visible rather than vanishing into one opaque bucket.
            by_dir: dict[str, list[Finding]] = defaultdict(list)
            for f in bucket_files:
                by_dir[f.parent].append(f)

            for dir_path, dir_files in sorted(
                by_dir.items(),
                key=lambda x: -sum(f.size_bytes for f in x[1]),
            ):
                total_sz = sum(f.size_bytes for f in dir_files)
                dir_name = _dir_display_name(dir_path)
                ent = SmartEntity(
                    path=dir_path,
                    name=f"Misc files in {dir_name}",
                    entity_type="mixed_folder",
                    size_bytes=total_sz,
                    file_count=len(dir_files),
                    folder_count=0,
                    modified=max((f.modified for f in dir_files), default=0),
                    accessed=max((f.accessed for f in dir_files), default=0),
                    children_sample=[f.name for f in dir_files[:15]],
                )
                ctx.emit_entity(ent)
                for f in dir_files:
                    ctx.claimed_paths.add(f.path.replace("\\", "/").lower())
                pass8_entities += 1

        else:
            total_sz = sum(f.size_bytes for f in bucket_files)
            label = _BUCKET_LABELS.get(bucket_type, "Uncategorized files")
            ent = SmartEntity(
                path=ctx.target_root,
                name=label,
                entity_type=bucket_type,
                size_bytes=total_sz,
                file_count=len(bucket_files),
                folder_count=0,
                modified=max((f.modified for f in bucket_files), default=0),
                accessed=max((f.accessed for f in bucket_files), default=0),
                children_sample=[f.name for f in bucket_files[:15]],
            )
            ctx.emit_entity(ent)
            for f in bucket_files:
                ctx.claimed_paths.add(f.path.replace("\\", "/").lower())
            pass8_entities += 1

    ctx.log(f"[smart]   → bucketed {len(loose_files):,} loose files into "
            f"{pass8_entities} entities")


def _low_value_suppression_reason(ent: SmartEntity) -> str:
    """Return a human-readable reason to hide a non-actionable entity."""
    if ent.entity_type == "duplicate_group":
        return ""

    name = (ent.name or "").strip().lower()
    leaf = os.path.basename((ent.path or "").rstrip("/\\")).strip().lower()
    sample_names = {
        os.path.basename(str(p)).strip().lower()
        for p in (ent.children_sample or [])
        if str(p).strip()
    }
    entity_names = {name, leaf} | sample_names

    if ent.file_count <= 0:
        return "contains no files"

    if ent.size_bytes <= 0:
        return "contains no meaningful bytes"

    if entity_names & _PLACEHOLDER_DIR_NAMES:
        if ent.size_bytes <= _LOW_VALUE_WEAK_BYTES:
            return "placeholder/test folder below the useful-size threshold"

    if sample_names and sample_names.issubset(_PLACEHOLDER_FILE_NAMES):
        return "contains only placeholder marker files"

    if ent.size_bytes <= _LOW_VALUE_BYTES:
        if ent.entity_type in _NON_ACTIONABLE_ENTITY_TYPES:
            return "tiny weakly-classified finding"
        if name in _PLACEHOLDER_DIR_NAMES or leaf in _PLACEHOLDER_DIR_NAMES:
            return "tiny placeholder folder"

    if (
        ent.entity_type in _NON_ACTIONABLE_ENTITY_TYPES
        and ent.file_count <= 1
        and ent.size_bytes <= _LOW_VALUE_WEAK_BYTES
    ):
        return "too small and unclassified to support a user decision"

    return ""


def _suppress_low_value_entities(
    ctx: "_DetectionContext",
    entities: list[SmartEntity],
) -> list[SmartEntity]:
    """Remove findings that are empty, placeholder-only, or non-actionable."""
    kept: list[SmartEntity] = []
    suppressed: dict[str, list[SmartEntity]] = defaultdict(list)

    for ent in entities:
        reason = _low_value_suppression_reason(ent)
        if reason:
            suppressed[reason].append(ent)
        else:
            kept.append(ent)

    if not suppressed:
        return kept

    total = sum(len(items) for items in suppressed.values())
    ctx.log(f"[smart] suppressed {total} low-value findings before UI")
    logged = 0
    for reason, items in sorted(suppressed.items(), key=lambda kv: -len(kv[1])):
        size = sum(e.size_bytes for e in items)
        ctx.log(f"[smart]   suppressed {len(items)} · {reason} · {_format_size(size)}")
        for ent in items[:3]:
            ctx.log(
                f"[smart]     - {ent.name} · {ent.category} · "
                f"{_format_size(ent.size_bytes)} · {ent.path}"
            )
            logged += 1
            if logged >= 12:
                break
        if logged >= 12:
            remaining = total - logged
            if remaining > 0:
                ctx.log(f"[smart]     ... {remaining} more suppressed findings")
            break

    return kept


def _postprocess(ctx: "_DetectionContext", t0: float) -> list:
    """Drop empty/aggregate entities, absorb sub-folder entities into
    parents, annotate cloud-sync and age, sort, and return the final list."""
    log_fn = ctx.log
    entities = ctx.entities

    n_before = len(entities)
    entities = [
        e for e in entities
        if not (
            e.path.replace("\\", "/").lower().rstrip("/") == ctx.root_norm
            and e.entity_type in ("unknown_folder", "mixed_folder")
        )
    ]
    if n_before - len(entities):
        log_fn("[smart] dropped scan-root aggregate entity")

    # Hide low-signal findings before they reach Findings. This keeps category
    # totals and AI queues focused on entries that can support a user decision.
    entities = _suppress_low_value_entities(ctx, entities)

    # 2. Absorb sub-folder entities into parent entities ("car vs wheel"):
    #    if entity B is strictly inside entity A, show only A.
    _HIGH_PRIORITY_ABSORBERS = {"application", "portable_app", "game",
                                "dev_artifacts", "ai_models"}
    _CONTENT_TYPES = {
        "document_folder", "photo_collection", "video_collection",
        "audio_collection", "media_collection", "archive_group",
        "backup_group", "log_folder", "cache_folder", "temp_folder",
        "database", "build_folder", "installer", "installer_group",
        "mixed_folder", "unknown_folder",
    }

    absorbers: list[tuple[str, str]] = []  # (norm_path, entity_type)
    for e in entities:
        np = e.path.replace("\\", "/").lower().rstrip("/")
        # An entity whose path IS the scan root (or a drive root) must never
        # absorb: Pass 8 loose-file buckets are created with path=target_root,
        # so a root-level "Loose AI model files" (ai_models) bucket would
        # otherwise swallow every other entity on the drive.
        if np == ctx.root_norm or _is_drive_root(np):
            continue
        if e.entity_type in _HIGH_PRIORITY_ABSORBERS:
            absorbers.append((np, e.entity_type))
        elif e.entity_type == "unknown_folder":
            absorbers.append((np, "unknown_folder"))
    absorbers.sort(key=lambda x: x[0].count("/"))

    def _owning_absorber(path: str):
        """The (norm_path, type) of the shallowest absorber containing path."""
        np = path.replace("\\", "/").lower().rstrip("/")
        for ap, atype in absorbers:
            if np != ap and np.startswith(ap + "/"):
                return ap, atype
        return None

    def _should_absorb(entity_path: str, entity_type: str) -> bool:
        owner = _owning_absorber(entity_path)
        if owner is None:
            return False
        _, owner_type = owner
        if owner_type in _HIGH_PRIORITY_ABSORBERS:
            if entity_type in ("node_modules", "venv", "dev_artifacts",
                               "shader_cache"):
                return False  # retain cleanup-value sub-entities
            return True
        if owner_type == "unknown_folder":
            return entity_type in _CONTENT_TYPES
        return False

    n_before = len(entities)
    entities = [e for e in entities
                if not _should_absorb(e.path, e.entity_type)]
    absorbed = n_before - len(entities)
    if absorbed:
        log_fn(f"[smart] absorbed {absorbed} sub-folder entities into parent "
               f"app/game entities")

    ctx.coverage_progress("complete")

    entities.sort(key=lambda e: e.size_bytes, reverse=True)
    elapsed = _time.time() - t0

    grouped_files = ctx.grouped_file_count
    unclaimed_count = max(0, ctx.total_files - grouped_files)
    coverage_pct = int(grouped_files / max(ctx.total_files, 1) * 100)

    log_fn(f"[smart] phase 2: assignment — claimed {grouped_files:,} "
           f"files, {unclaimed_count:,} untracked")
    log_fn(f"[smart] semantic grouping complete · {ctx.entities_created} "
           f"entities created · {grouped_files:,} files grouped · "
           f"{coverage_pct}% coverage · {unclaimed_count:,} items "
           f"ungrouped · {elapsed:.2f}s")
    if unclaimed_count > 0:
        log_fn(f"[smart] note: {unclaimed_count:,} items not claimed (dirs "
               f"already swept above or empty)")

    # ── Cloud-sync annotation ──────────────────────────────────────
    try:
        from app.services.cloud_detector import detect_cloud_roots, is_cloud_path
        cloud_roots = detect_cloud_roots()
        if cloud_roots:
            cloud_count = 0
            for provider_path, provider_name in cloud_roots.items():
                log_fn(f"[cloud] detected {provider_name} sync root at "
                       f"{provider_path}")
            for e in entities:
                provider = is_cloud_path(e.path, cloud_roots)
                if provider:
                    e.cloud_sync_provider = provider
                    if e.risk == "Safe":
                        e.risk = "Review"
                        e.risk_reason = f"cloud-synced ({provider})"
                    cloud_count += 1
            if cloud_count:
                log_fn(f"[cloud] {cloud_count} entities in cloud-synced paths")
    except Exception as _cloud_err:
        log_fn(f"[cloud] detection skipped: {_cloud_err}")

    # ── Age-based heuristic boosts ─────────────────────────────────
    _AGE_ELIGIBLE = frozenset({
        "dev_artifacts", "dev_project", "installer_group", "installer",
        "archive_group", "build_folder", "venv", "node_modules",
        "temp_folder", "cache_folder", "log_folder", "ai_cache",
    })
    _SECS_2Y = 2 * 365.25 * 86400
    _SECS_5Y = 5 * 365.25 * 86400
    _now = _time.time()
    age_boosted = 0
    for _e in entities:
        if _e.entity_type not in _AGE_ELIGIBLE or not _e.modified:
            continue
        age_s = _now - _e.modified
        if age_s >= _SECS_5Y:
            _e.age_boost = 0.4
            if _e.risk_reason and "old" not in _e.risk_reason:
                _e.risk_reason += f" · {_e.age} old"
            age_boosted += 1
        elif age_s >= _SECS_2Y:
            _e.age_boost = 0.2
            if _e.risk_reason and "old" not in _e.risk_reason:
                _e.risk_reason += f" · {_e.age} old"
            age_boosted += 1
    if age_boosted:
        log_fn(f"[age] {age_boosted} entities flagged as stale (2y+ since "
               f"last modification)")

    log_fn("[smart] entities ready for dashboard")
    return entities


def detect_entities(
    findings: list[Finding],
    target_root: str,
    log_fn=None,
    progress_fn=None,
    entity_fn=None,
    extra_monolith_patterns=None,
) -> list[SmartEntity]:
    """Detect SmartEntities from a list of raw findings.

    Runs a container-first discovery phase followed by an ordered pipeline
    of classification passes (see the _phase1_* / _pass*_ functions). Every
    scanned item ends up inside exactly one entity -- the Containment Rule.

    Args:
        findings: List of Finding objects from a filesystem scan.
        target_root: Root path being scanned.
        log_fn: Optional callback for log messages (str -> None).
        progress_fn: Optional callback for progress updates.
        entity_fn: Optional callback when an entity is discovered
            (SmartEntity -> None) -- used for streaming AI queueing.
        extra_monolith_patterns: Optional caller-supplied monolith name
            patterns merged with the built-in set.

    Returns a list of SmartEntity objects, sorted by size descending.
    """
    if log_fn is None:
        log_fn = lambda s: None
    if progress_fn is None:
        progress_fn = lambda phase, grouped_files, ungrouped_files, entities_created, coverage_pct=0: None
    if entity_fn is None:
        entity_fn = lambda e: None

    t0 = _time.time()
    ctx = _DetectionContext(findings, target_root, log_fn, progress_fn, entity_fn)
    ctx.coverage_progress("started")

    # Merge built-in monolith patterns with caller-supplied extras.
    extra_pats: tuple[str, ...] = ()
    if extra_monolith_patterns:
        extra_pats = tuple(
            p.lower().strip() for p in extra_monolith_patterns if p.strip()
        )

    # PHASE 1 -- discovery (claim known monolith roots first).
    _phase1_discovery(ctx, extra_pats)

    # PHASE 2 -- assignment (classify everything not yet claimed).
    log_fn("[smart] phase 2: assignment — running semantic classification "
           "pipeline")
    # Treat user-profile containers (C:/Users) as structural nodes only.
    for f in ctx.all_dirs:
        norm_path = f.path.replace("\\", "/").lower().rstrip("/")
        if _is_user_container_dir(norm_path):
            ctx.claimed_paths.add(norm_path)

    _pass0_update_caches(ctx)
    _pass1_known_dirs(ctx)
    _pass2_installed_apps(ctx)
    _pass2b_app_markers(ctx)
    _pass3_browser_profiles(ctx)
    _pass3b_games(ctx)
    _pass4_cache_folders(ctx)
    _pass5_protected(ctx)
    _pass6_content_folders(ctx)
    _pass7_sweep(ctx)
    _pass8_loose_files(ctx)

    return _postprocess(ctx, t0)


# ── Internal helpers ──────────────────────────────────────────────

def _build_entity(
    path: str,
    name: str,
    entity_type: str,
    children: list[Finding],
    reason: str,
    parent_app: str = "",
    parent_app_path: str = "",
    is_internal: bool = False,
    depth: int = 0,
    app_version: str = "",
    app_publisher: str = "",
    install_date: str = "",
) -> SmartEntity:
    """Create a SmartEntity from a path and its children."""
    corrected_type, corrected_reason = _safety_correct_entity_type(path, entity_type)
    if corrected_type != entity_type:
        entity_type = corrected_type
        reason = corrected_reason or reason
        if entity_type == "user_profile":
            base = os.path.basename(path.rstrip("/\\"))
            name = f"{base} Profile" if base else "User Profile"
        elif entity_type == "application_data":
            name = "Application Packages"
        elif entity_type == "installer_cache":
            name = "NVIDIA Update Cache"

    files = [c for c in children if not c.is_dir]
    dirs = [c for c in children if c.is_dir]
    total_size = sum(c.size_bytes for c in children)
    mtime = max((c.modified for c in children), default=0)
    atime = max((c.accessed for c in children), default=0)

    # Sample child names for AI context (varied set)
    sample = []
    seen_exts = set()
    for c in children[:50]:
        ext = c.extension.lower() if c.extension else ""
        if ext not in seen_exts or len(sample) < 8:
            sample.append(c.name)
            seen_exts.add(ext)
        if len(sample) >= 15:
            break

    return SmartEntity(
        path=path,
        name=name,
        entity_type=entity_type,
        size_bytes=total_size,
        file_count=len(files),
        folder_count=len(dirs),
        modified=mtime,
        accessed=atime,
        risk_reason=reason,
        children_sample=sample,
        parent_app=parent_app,
        parent_app_path=parent_app_path,
        is_internal=is_internal,
        depth=depth,
        app_version=app_version,
        app_publisher=app_publisher,
        install_date=install_date,
    )


def _classify_by_content(children: list[Finding]):
    """Classify a group of findings by extension distribution.

    Returns entity_type or None if no clear pattern.
    """
    if not children:
        return None

    files = [c for c in children if not c.is_dir]
    if not files:
        return None

    ext_counts: dict[str, int] = defaultdict(int)
    ext_sizes: dict[str, int] = defaultdict(int)

    for f in files:
        ext = f.extension.lower() if f.extension else ""
        ext_counts[ext] += 1
        ext_sizes[ext] += f.size_bytes

    total = len(files)
    total_sz = sum(ext_sizes.values()) or 1  # avoid div/0

    def _ratio(ext_set: set) -> float:
        matched = sum(ext_counts.get(e, 0) for e in ext_set)
        return matched / total if total > 0 else 0

    def _size_ratio(ext_set: set) -> float:
        matched = sum(ext_sizes.get(e, 0) for e in ext_set)
        return matched / total_sz

    # Dominant content type detection (>50% by count OR >70% by size)

    # Virtual machine storage — disk/config files are large and high-risk.
    if _ratio(_VM_DISK_EXTS) > 0.2 or _size_ratio(_VM_DISK_EXTS) > 0.4:
        return "vm_storage"
    
    # Creative Projects (Media → Projects) - high priority
    if _ratio(_PROJECT_EXTS) > 0.3 or _size_ratio(_PROJECT_EXTS) > 0.4:
        return "creative_project"
    
    # AI/ML Models - high priority (large files, specific extensions)
    if _ratio(_MODEL_EXTS) > 0.2 or _size_ratio(_MODEL_EXTS) > 0.4:
        return "ai_models"
    
    # Images
    if _ratio(_IMAGE_EXTS) > 0.5 or _size_ratio(_IMAGE_EXTS) > 0.7:
        # Check for photogrammetry datasets (mix of images + point clouds)
        if _ratio(_PHOTOGRAMMETRY_EXTS) > 0.1:
            return "dataset"
        return "photo_collection"
    
    # Videos
    if _ratio(_VIDEO_EXTS) > 0.5 or _size_ratio(_VIDEO_EXTS) > 0.7:
        return "video_collection"
    
    # Audio
    if _ratio(_AUDIO_EXTS) > 0.5 or _size_ratio(_AUDIO_EXTS) > 0.7:
        return "audio_collection"
    
    # Mixed Media (if has both images and videos)
    if (_ratio(_IMAGE_EXTS) > 0.2 and _ratio(_VIDEO_EXTS) > 0.2) or \
       (_size_ratio(_IMAGE_EXTS) > 0.3 and _size_ratio(_VIDEO_EXTS) > 0.3):
        return "media_collection"
    
    # Documents
    if _ratio(_DOC_EXTS) > 0.5 or _size_ratio(_DOC_EXTS) > 0.7:
        return "document_folder"
    
    # Archives
    if _ratio(_ARCHIVE_EXTS) > 0.5:
        return "archive_group"
    
    # Installers
    if _ratio(_INSTALLER_EXTS) > 0.4:
        return "installer_group"
    
    # Databases
    if _ratio(_DATABASE_EXTS) > 0.3 or _size_ratio(_DATABASE_EXTS) > 0.5:
        return "database"
    
    # Backups
    if _ratio(_BACKUP_EXTS) > 0.3:
        return "backup_group"
    
    # Logs
    if _ratio(_LOG_EXTS) > 0.5:
        return "log_folder"
    
    # Photogrammetry
    if _ratio(_PHOTOGRAMMETRY_EXTS) > 0.3:
        return "dataset"

    return None
