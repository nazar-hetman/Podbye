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
from collections import defaultdict, deque, Counter
from pathlib import Path
from typing import Optional

from app.models.finding import (Finding, _format_size, is_model_blob,
                                _AI_ML_PATH_KEYWORDS)
from app.models.smart_entity import SmartEntity, ENTITY_TYPES, _ENTITY_RISK
from app.models.reasons import Reason
from app.models.risk import RISK_PROTECTED
from app.services.app_presence import presence as _app_presence, PRESENT as _PRESENT


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
            f"Podbye: cannot load classification rules from {_RULES_PATH}: {exc}"
        ) from exc


_RULES = _load_classification_rules()


# ═══════════════════════════════════════════════════════════════════════════
# WINDOWS REGISTRY INTEGRATION (for installed app detection)
# ═══════════════════════════════════════════════════════════════════════════

_INSTALLED_PROGRAMS_CACHE: dict | None = None


def _get_installed_programs(force_refresh: bool = False) -> dict[str, dict]:
    """Query Windows registry for installed programs.

    Returns dict mapping normalized install path → program info. Cached for the
    process lifetime — the installed-software set rarely changes mid-session, so
    every scan re-reading the registry was wasted work. Pass force_refresh=True
    to rebuild (e.g. after an uninstall).
    """
    global _INSTALLED_PROGRAMS_CACHE
    if _INSTALLED_PROGRAMS_CACHE is not None and not force_refresh:
        return _INSTALLED_PROGRAMS_CACHE

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

                                    # Uninstaller command. The INTERACTIVE
                                    # string is preferred, not the quiet one:
                                    # Deep Uninstall is a button the user
                                    # pressed, so they should see the app's own
                                    # wizard and know what happened. A /SILENT
                                    # run that fails — and most need elevation
                                    # — is indistinguishable from one that
                                    # worked, which is exactly how this
                                    # appeared to "exist but not work".
                                    uninstall = ""
                                    for _val in ("UninstallString", "QuietUninstallString"):
                                        try:
                                            uninstall, _ = winreg.QueryValueEx(subkey, _val)
                                            if uninstall:
                                                break
                                        except OSError:
                                            pass

                                    if install_loc:
                                        norm_path = install_loc.replace("\\", "/").lower().rstrip("/")
                                        installed[norm_path] = {
                                            "name": name,
                                            "publisher": publisher,
                                            "path": install_loc,
                                            "uninstall_string": uninstall,
                                        }
                                except OSError:
                                    pass
                        except OSError:
                            pass
            except OSError:
                pass
                
    except ImportError:
        pass  # Not on Windows

    _INSTALLED_PROGRAMS_CACHE = installed
    return installed


def invalidate_installed_programs_cache():
    """Drop the cached registry snapshot (e.g. after an uninstall)."""
    global _INSTALLED_PROGRAMS_CACHE
    _INSTALLED_PROGRAMS_CACHE = None


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
# A .msi/.msix/.appx/.dmg IS an installer — the extension says so on its own.
# A .exe is just an executable, and an installed program's folder is mostly
# .exe files, so ".exe" alone must never classify anything as an installer;
# see _looks_like_installer_file for the filename evidence that does.
_INSTALLER_PACKAGE_EXTS = {".msi", ".msix", ".appx", ".dmg"}
_INSTALLER_EXTS = _INSTALLER_PACKAGE_EXTS | {".exe"}
_MODEL_EXTS = {".gguf", ".bin", ".safetensors", ".pt", ".pth", ".onnx",
               ".ckpt", ".h5", ".tflite", ".ggml"}

# Extensions that mean "model weights" on their own, and the ones that only
# mean it in context. ".bin" is the whole problem: C:/AMTAG.BIN is a 1 KB
# motherboard tag file, and it was presented to the user as "Loose AI model
# files in C:" — a row in the AI/ML category naming no file at all.
_MODEL_EXTS_ALWAYS = {".gguf", ".safetensors", ".pt", ".pth", ".onnx",
                      ".ckpt", ".ggml"}
_MODEL_EXTS_IN_CONTEXT = {".bin", ".h5", ".tflite", ".pkl", ".pb"}
# Matches finding.categorize's threshold for the same extensions.
_MODEL_CONTEXT_MIN_BYTES = 50 * 1024 * 1024
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

# "cache" as a whole word, with its ordinary endings. Not a substring test:
# that would take "cachet" with it.
_CACHE_WORD_RE = re.compile(r"cach(e|es|ed|ing)[0-9]*$", re.I)


# Directories whose children are packages, not folders someone named.
_PACKAGE_CONTAINERS = ("node_modules", "site-packages", "dist-packages",
                       "bower_components", "jspm_packages", "vendor")


def _inside_package_container(norm_path: str) -> bool:
    """True when *norm_path* sits inside a package directory."""
    segments = norm_path.split("/")
    return any(seg in _PACKAGE_CONTAINERS for seg in segments[:-1])


def _named_exactly_like_cache(name: str) -> bool:
    """The long-standing test: the name *is* a cache word, or ends in one."""
    lower = (name or "").lower()
    return (lower in _CACHE_KEYWORDS or lower.endswith("cache")
            or lower.endswith("tmp"))


def _has_cache_word(name: str) -> bool:
    """True when "cache" stands as its own word somewhere in *name*.

    Pass 4's test used to be ``name == "cache"`` or ``name.endswith("cache")``,
    which recognises ``GPUCache`` and misses ``Media Cache Files`` — 1.5 GB of
    Adobe's rebuildable media cache, therefore never offered as anything, on
    the machine whose owner asked why Podbye would not clear Adobe's caches.
    Also missed: ``Cache Storage``, ``Code Cache``, ``cache2``.

    Per token, not a substring: ``cachet`` and ``apache`` are not caches, while
    ``cache2`` and ``Caches`` are.
    """
    lower = (name or "").lower()
    return any(_CACHE_WORD_RE.fullmatch(token)
               for token in re.split(r"[^a-z0-9]+", lower) if token)

# Known cache/installer/log parent app hints: path segment → friendly description
# Used to annotate "Cache for X", "Installer – X", "Logs – X" rather than bare names
_CACHE_SOURCE_HINTS: dict = dict(_RULES["cache_source_hints"])

# ── Protected path detection ─────────────────────────────────────

# System-critical roots — protected ONLY when they sit at the drive root
# (e.g. C:/Windows), never when the same word appears deeper in a path. A
# game's ".../Saved/Config/Windows" folder is not the operating system.
# ProgramData is intentionally NOT here: it holds plenty of reclaimable vendor
# data and should be scanned and classified, not blanket-protected.
_PROTECTED_ROOT_DIR_NAMES = {
    "windows", "recovery", "boot",
    "$windows.~bt", "$windows.~ws",
    "msocache", "perflogs", "config.msi",
    "system volume information",
}

# Container paths that should NOT become System entities if they contain
# meaningful child entities (like applications). These are treated as 
# organizational folders, not protected system paths.
_CONTAINER_DIR_NAMES = {
    "program files", "program files (x86)",
}

# Sensitive folders under AppData that stay protected wherever they appear.
# NOTE: a blanket "microsoft" entry used to live here, which swept the ENTIRE
# AppData/Local/Microsoft tree (Edge cache, Teams, Office caches, WER, INetCache
# — almost all regenerable) into the "System" category. That was misleading:
# only the credential/crypto stores below are genuinely system-protected, and
# they match by their own names regardless of the "Microsoft" parent. Everything
# else under Microsoft is now left to classify normally (cache, app data, ...).
_PROTECTED_APPDATA_DIRS = {
    "local settings",
    "credential", "credentials", "vault",
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

    The qualifier names the *data*, not the person — the account name is
    deliberately skipped, so a profile-level "Documents" stays "Documents"
    rather than "Documents – Nazar".

    Examples:
      "documents"  at c:/users/nazar/documents           → "Documents"
      "cache"      at c:/users/nazar/appdata/local/discord/cache → "Cache – Discord"
      "videos"     at c:/steamapps/common/portal2/videos  → "Videos – Portal2"
      "node_modules" (not generic)                        → "node_modules"  (unchanged)
    """
    lower = folder_name.lower()
    if lower not in _GENERIC_FOLDER_NAMES:
        return folder_name

    norm = folder_path.replace("\\", "/").lower()
    parts = norm.split("/")

    # The segment right after C:/Users (or /home) is the account name — it
    # identifies the user, not the content, so it is never a useful qualifier.
    usernames = {
        parts[i + 1] for i, seg in enumerate(parts[:-1])
        if seg in _USER_CONTAINER_NAMES and i + 1 < len(parts)
    }

    # Walk path segments from right-to-left, skipping the folder itself
    for part in reversed(parts[:-1]):
        if not part or part.endswith(":"):          # skip drive letters
            continue
        if (part in _QUALIFIER_SKIP_SEGS or part in _GENERIC_FOLDER_NAMES
                or part in usernames):
            continue
        # Found a meaningful qualifier — title-case it nicely
        qualifier = part.replace("-", " ").replace("_", " ").title()
        return f"{folder_name.title()} – {qualifier}"

    return folder_name.title()


# ═══════════════════════════════════════════════════════════════════════════
# GAME-SAVE CONTEXT RESOLUTION
# ═══════════════════════════════════════════════════════════════════════════
# A bare "Game Saves" entity is not actionable: the user cannot tell *whose*
# saves they are or whether the owning game is still installed. These helpers
# resolve the owning game from the save path and cross-reference it against the
# games/apps detected in the same scan + the Windows registry, so the entity
# can be named "Skyrim Saves — game still installed" instead of just "Saves".

# Folder names that hold one sub-folder PER GAME (game name is the segment
# immediately AFTER the marker).
_SAVE_CONTAINER_MARKERS = {"saved games", "savedgames", "my games", "mygames"}

# Folder names that ARE a single game's save dir (game name is the nearest
# meaningful ANCESTOR segment).
_SAVE_LEAF_MARKERS = {
    "saves", "save", "sav", "savegame", "savegames", "savedata", "save data",
    "userdata", "playerprofiles", "player profiles", "profiles", "storage",
}

# Tokens stripped when normalising a game name for matching, so
# "The Witcher 3: Wild Hunt - GOTY Edition" and a registry "Witcher 3" align.
_GAME_NAME_NOISE = {
    "the", "a", "of", "game", "edition", "remastered", "remaster", "definitive",
    "goty", "year", "deluxe", "complete", "hd", "ultimate", "enhanced",
    "deluxe", "standard", "collection", "anniversary", "directors", "cut",
}


def _pretty_game(name: str) -> str:
    """Title-case a game name without mangling apostrophes (Baldur's, not Baldur'S)."""
    return " ".join(w[:1].upper() + w[1:] if w else w for w in name.split())


def _clean_game_name(leaf: str) -> str:
    """Strip a save-engine build-id suffix from a per-game folder name.

    Ren'Py names each game's save folder ``<Game>-<buildid>`` (e.g.
    ``MyOfficeAdventures-1602343789``); the trailing number is machine noise, not
    part of the title. Also drops a trailing ``_<digits>``. The base name is kept
    verbatim (camel-case preserved) so ``MyOfficeAdventures`` stays readable.
    """
    name = (leaf or "").strip()
    name = re.sub(r"[-_]\d{6,}$", "", name)   # RenPy/build id: 6+ trailing digits
    return name or leaf


# Save engines that store one folder PER game directly under an engine folder
# (the child folder name IS the game), unlike "Saved Games" which nests game
# folders. Extend this set to cover more engines — the detection, name-cleaning
# and installed/created enrichment below are all engine-agnostic.
_PER_GAME_SAVE_ENGINES = {
    "renpy",   # Ren'Py: %APPDATA%/RenPy/<Game>-<buildid>
    "love",    # LÖVE (Love2D): %APPDATA%/LOVE/<Game>
    "rpgmvcooking", "krkr", "kirikiri",  # common VN/2D engines
}


def _folder_created_date(path: str) -> str:
    """Best-effort creation date (YYYY-MM-DD) for a folder, or "" if unknown.

    On Windows st_ctime is the real creation time. The scan model only carries
    mtime, so this stats the path fresh; guarded because a restored session may
    reference a path that no longer exists.
    """
    try:
        if path and os.path.exists(path):
            import time as _t
            return _t.strftime("%Y-%m-%d", _t.localtime(os.path.getctime(path)))
    except OSError:
        pass
    return ""


def _normalize_game_name(name: str) -> str:
    """Reduce a game/app name to a comparable token string.

    Lowercases, drops punctuation and edition/version noise words, so two
    differently-decorated names for the same game compare equal-ish.
    """
    s = (name or "").lower().replace("_", " ").replace("-", " ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    toks = [t for t in s.split() if t and t not in _GAME_NAME_NOISE]
    return " ".join(toks)


def _extract_owning_game(norm_path: str) -> str:
    """Best-effort owning-game folder name from a save path (lowercased, raw).

    Returns '' when the path gives no usable game name (e.g. the entity is a
    multi-game container like '.../Saved Games').

    Examples:
      .../documents/my games/skyrim/saves        → "skyrim"
      .../saved games/cyberpunk 2077             → "cyberpunk 2077"
      .../appdata/local/larian studios/baldur's gate 3/playerprofiles → "baldur's gate 3"
      .../steamapps/common/stardew valley/saves  → "stardew valley"
    """
    raw = [p for p in norm_path.rstrip("/").split("/") if p and not p.endswith(":")]
    if not raw:
        return ""

    # The segment immediately after C:/Users (or /home) is a username, never a
    # game — mark it so the ancestor walk skips it.
    full = [p for p in norm_path.rstrip("/").split("/") if p]
    usernames = {
        full[i + 1] for i, seg in enumerate(full[:-1])
        if seg in _USER_CONTAINER_NAMES
    }

    def _is_game_segment(seg: str) -> bool:
        return (seg not in _SAVE_LEAF_MARKERS
                and seg not in _SAVE_CONTAINER_MARKERS
                and seg not in _QUALIFIER_SKIP_SEGS
                and seg not in _GENERIC_FOLDER_NAMES
                and seg not in usernames)

    # Case 0 — per-game save engine (Ren'Py): the segment after the engine
    # folder is the game itself, with a build-id suffix to strip.
    for i in range(len(raw) - 1):
        if raw[i] in _PER_GAME_SAVE_ENGINES:
            return _clean_game_name(raw[i + 1]).lower()

    # Case 1 — segment right after a "saved games" / "my games" container.
    for i in range(len(raw) - 1):
        if raw[i] in _SAVE_CONTAINER_MARKERS and _is_game_segment(raw[i + 1]):
            return raw[i + 1]

    # Case 2 — the leaf itself is a save marker → nearest meaningful ancestor.
    if raw[-1] in _SAVE_LEAF_MARKERS:
        for seg in reversed(raw[:-1]):
            if _is_game_segment(seg):
                return seg
    return ""


def _game_is_installed(game_norm: str, known_games: set) -> bool:
    """Return True if a normalised game name plausibly matches a known game/app.

    Matching is deliberately fuzzy (substring + token overlap) because save
    folder names and registry/library names rarely agree character-for-character.
    """
    if not game_norm or len(game_norm) < 2:
        return False
    g_tokens = set(game_norm.split())
    for known in known_games:
        if not known:
            continue
        if game_norm == known:
            return True
        # Substring match only when the shorter side is distinctive (>=4 chars),
        # so "ark" doesn't match "darksiders".
        if len(game_norm) >= 4 and (game_norm in known or known in game_norm):
            return True
        # Two or more shared significant tokens is a strong signal.
        if len(g_tokens & set(known.split())) >= 2:
            return True
    return False


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


# Content-collection entity types and the extensions that justify them. Used to
# CONFIRM a name-based guess against the folder's actual files, so a folder
# called "Videos" with no videos (or "Windows Photo Viewer", which only has
# executables) is not trusted on its name alone.
_CONTENT_TYPE_EXTS = {
    "photo_collection": _IMAGE_EXTS,
    "video_collection": _VIDEO_EXTS,
    "audio_collection": _AUDIO_EXTS,
    "document_folder":  _DOC_EXTS,
    "media_collection": _IMAGE_EXTS | _VIDEO_EXTS | _AUDIO_EXTS,
}


def _content_confirms_type(children: list, etype: str, min_ratio: float = 0.3,
                           min_size_ratio: float = 0.3) -> bool:
    """True if a folder's actual files back up a name-based content guess.

    Only gates the content-collection types in _CONTENT_TYPE_EXTS; every other
    type passes through unchanged. An empty folder never confirms a media type.

    Counted two ways on purpose. A count-only test asks "are most of the files
    documents?", but the number the entity then reports is its *size* — so a
    folder of 19 small .docx beside a .mp4 and a .zip passed at 66% by count
    while documents were 23% of its bytes, and the user was shown 2 GB labelled
    "Documents". Reported as a bug against E:/Work/Projects/Focus/Docs. The
    label has to be justified by the same measure it is displayed with.
    """
    exts = _CONTENT_TYPE_EXTS.get(etype)
    if exts is None:
        return True
    files = [c for c in children if not c.is_dir]
    if not files:
        return False
    matched = [f for f in files if (f.extension or "").lower() in exts]
    if (len(matched) / len(files)) < min_ratio:
        return False
    total_size = sum(f.size_bytes for f in files)
    if total_size <= 0:
        return True          # nothing to weigh; the count is all we have
    return (sum(f.size_bytes for f in matched) / total_size) >= min_size_ratio


# ── App / UI asset folders vs. genuine user media ─────────────────
# An "images"/"assets" folder full of icons, sprites and web graphics is an
# application's internal content — NOT the user's photo collection. Surfacing it
# under the "Images" category mixes app UI art in with personal photos, so the
# user can no longer tell what is actually theirs. These folders are redirected
# to "application_data" (review-only app support content) instead.

# Folder names that denote an asset/UI bundle rather than a media library.
_ASSET_DIR_NAMES = {
    "assets", "asset", "static", "public", "resources", "resource", "res",
    "img", "imgs", "icons", "icon", "sprites", "sprite", "textures", "texture",
    "drawable", "mipmap", "skin", "skins", "theme", "themes", "ui",
    "graphics", "gfx", "art",
}
# Image formats typical of UI / web assets (vector, icon, lossless web).
_ASSET_IMAGE_EXTS = {".svg", ".ico", ".png", ".gif", ".webp"}
# Photographic formats — their presence signals a genuine user photo library.
_PHOTO_IMAGE_EXTS = {".jpg", ".jpeg", ".heic", ".heif", ".tif", ".tiff",
                     ".cr2", ".nef", ".arw", ".dng", ".raw", ".rw2",
                     ".orf", ".pef"}


def _looks_like_app_assets(norm_path: str, folder_name: str, files: list) -> bool:
    """True when an image-dominant folder is app/UI asset content, not user media.

    Conservative on purpose — it must never reclassify a real photo library:
      1. there is at least one image and NO photographic-format image, and
      2. the folder is structurally an app asset bundle (an app-internal
         ancestor such as resources/static/src, or its own name is an
         asset-bundle name), and
      3. every image present is a web/UI format (svg/ico/png/gif/webp).
    """
    img_files = [f for f in files if (f.extension or "").lower() in _IMAGE_EXTS]
    if not img_files:
        return False
    # Any genuine photograph → treat as user media, never as app assets.
    if any((f.extension or "").lower() in _PHOTO_IMAGE_EXTS for f in img_files):
        return False
    structural = (folder_name.lower() in _ASSET_DIR_NAMES
                  or _is_internal_path(norm_path)[0])
    if not structural:
        return False
    return all((f.extension or "").lower() in _ASSET_IMAGE_EXTS for f in img_files)


# Media entity types that should be re-checked for being app/UI assets.
_USER_IMAGE_MEDIA_TYPES = frozenset({"photo_collection", "media_collection"})


# ── Human-readable naming helpers ─────────────────────────────────
# Used to give understanding to otherwise-opaque entities (unrecognised or
# mixed folders, cryptic GUID/hash names) WITHOUT dumping raw filenames — we
# describe the *kind* of content instead.

_SOURCE_CODE_EXTS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".c", ".cpp", ".h", ".hpp", ".cs",
    ".java", ".go", ".rs", ".rb", ".php", ".lua", ".sh", ".ps1", ".bat",
}
_CONFIG_EXTS = {
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".xml", ".html", ".css",
}
_CODE_EXTS = _SOURCE_CODE_EXTS | _CONFIG_EXTS

_GUID_RE = re.compile(
    r"^\{?[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\}?$", re.I
)
_HEX_BLOB_RE = re.compile(r"^[0-9a-f]{16,}$", re.I)


def _ext_group(ext: str) -> str:
    """Map a file extension to a friendly content-group word, or '' if unknown."""
    e = (ext or "").lower()
    if e in _IMAGE_EXTS:     return "images"
    if e in _VIDEO_EXTS:     return "videos"
    if e in _AUDIO_EXTS:     return "audio"
    if e in _DOC_EXTS:       return "documents"
    if e in _ARCHIVE_EXTS:   return "archives"
    if e in _INSTALLER_PACKAGE_EXTS: return "installers"
    # A folder full of .exe files is a program, not a pile of installers.
    if e == ".exe":          return "programs"
    if e in _MODEL_EXTS:     return "AI models"
    if e in _DATABASE_EXTS:  return "databases"
    if e in _LOG_EXTS or e in _BACKUP_EXTS: return "logs & backups"
    if e in _CODE_EXTS:      return "code & config"
    return ""


def _is_model_file(f) -> bool:
    """True only when a file really is model weights.

    Pass 8 used to accept any ``.bin``/``.h5`` *or* anything whose category
    had come out "AI / ML". Both leak. The category is assigned by path, and
    every file under ``miniconda3`` gets it — which is how nine copies of
    setuptools' two-file ``tests/config/downloads`` fixture (``preload.py``,
    ``__init__.py``, 2 KB each) became nine "Loose AI model files" rows, and
    how a 1 KB ``AMTAG.BIN`` at the drive root became a tenth. Together they
    were ten of the nineteen rows in the AI/ML category on a real scan.

    So: the unambiguous extensions always count; the ambiguous ones need
    either real weight-file size or a model store in the path; and an
    extensionless blob is judged exactly as ``finding.categorize`` judges it,
    which is what keeps Ollama's and HuggingFace's hash-named weights.
    """
    ext = (getattr(f, "extension", "") or "").lower()
    if ext in _MODEL_EXTS_ALWAYS:
        return True
    parts = frozenset(
        p for p in str(getattr(f, "path", "")).replace("\\", "/").lower().split("/") if p
    )
    size = int(getattr(f, "size_bytes", 0) or 0)
    if ext in _MODEL_EXTS_IN_CONTEXT:
        return size >= _MODEL_CONTEXT_MIN_BYTES or bool(parts & _AI_ML_PATH_KEYWORDS)
    if not ext:
        return is_model_blob(str(getattr(f, "name", "")).lower(), ext, parts, size)
    return False


# A kind has to be at least this much of a folder to be worth naming in its
# description. Below it the phrase is trivia: three .ico files among a
# thousand made a build tree read as "code & config and images".
_DESCRIPTOR_MIN_SHARE = 0.1


def _content_descriptor(children: list) -> str:
    """A short phrase describing what a folder holds, e.g. 'mostly images'.

    Returns '' when nothing recognisable dominates. Never lists filenames.

    Shares are of ALL the files, including the ones no kind claims. Counting
    only the recognised ones let a folder that is two-thirds .dll and .lib be
    described as "mostly code & config" on the strength of its headers — and
    the classifier, which counts every file, then disagreed with the very name
    it was printing. A row that describes itself as code and is filed under
    Unknown was one of the things reported.
    """
    files = [c for c in children if not c.is_dir]
    if not files:
        return ""
    counts = Counter(g for g in (_ext_group(f.extension) for f in files) if g)
    if not counts:
        return ""
    total = len(files)
    top = [(name, n) for name, n in counts.most_common(2)
           if n / total >= _DESCRIPTOR_MIN_SHARE]
    if not top:
        return ""
    g0, n0 = top[0]
    if n0 / total >= 0.6:
        return f"mostly {g0}"
    if len(top) >= 2:
        return f"{top[0][0]} and {top[1][0]}"
    return g0


def _looks_cryptic(name: str) -> bool:
    """True for opaque folder names (GUIDs, long hashes) that tell a user nothing."""
    n = name.strip().strip("{}")
    if _GUID_RE.match(name) or _HEX_BLOB_RE.match(n):
        return True
    # Long, vowel-less, no separators → likely a random/hashed token.
    if len(n) >= 12 and n.isalnum() and not any(v in n.lower() for v in "aeiou"):
        return True
    return False


# Above this share of files that match no known kind, the honest description
# of a folder is that we do not recognise what is in it — unless some kind is
# still a big enough part of it to be worth naming instead.
_UNRECOGNISED_DOMINANT = 0.6
_KIND_BEATS_UNRECOGNISED = 0.35


def _unrecognised_share(children: list) -> tuple:
    """(share matching no known kind, share of the commonest kind)."""
    files = [c for c in children if not c.is_dir]
    if not files:
        return 0.0, 0.0
    groups = [_ext_group(f.extension) for f in files]
    unknown = sum(1 for g in groups if not g)
    known = Counter(g for g in groups if g)
    top = known.most_common(1)[0][1] if known else 0
    return unknown / len(files), top / len(files)


def _descriptive_folder_name(folder_name: str, folder_path: str, children: list) -> str:
    """Best-effort understandable name for an unrecognised / mixed folder.

    Cryptic names become 'Unrecognized folder'; a content descriptor is appended
    when available, so the user learns what is inside without raw filenames.

    When no kind reaches a share worth naming, why is worth saying. 77% of
    E:/Forge/investigations is .dat, .dmap, .result and .exif — extensions
    Podbye has no rule for — and that, not "mostly code & config", is the
    reason the row sits in Unknown.
    """
    base = "Unrecognized folder" if _looks_cryptic(folder_name) \
        else _qualify_folder_name(folder_name, folder_path)
    desc = _content_descriptor(children)
    unknown_share, top_share = _unrecognised_share(children)
    if (unknown_share >= _UNRECOGNISED_DOMINANT
            and top_share < _KIND_BEATS_UNRECOGNISED):
        desc = "mostly unrecognized file types"
    return f"{base} · {desc}" if desc else base


def _is_protected_path(norm_path: str) -> bool:
    """Check if a path is system-critical or protected.

    Depth-aware: a system root (Windows/Recovery/Boot/…) is only protected when
    it is the FIRST segment under the drive (C:/Windows/…), so nested folders
    that merely share the name (e.g. a game's Saved/Config/Windows) are not
    swept into "System". Credential/crypto stores under AppData stay protected
    wherever they appear.
    """
    parts = norm_path.rstrip("/").split("/")
    # parts[0] = drive (c:), parts[1] = top-level dir.
    if len(parts) >= 2 and parts[1] in _PROTECTED_ROOT_DIR_NAMES:
        return True
    if "appdata" in parts:
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


# Path segments that describe structure, not identity. "local" is a substring of
# "LocalSend version 1.17.0" — which is how every database under AppData\Local
# came to be labelled as LocalSend's.
_GENERIC_PATH_SEGMENTS = {
    "users", "user", "appdata", "local", "locallow", "roaming", "temp", "tmp",
    "data", "settings", "config", "cache", "caches", "default", "profile",
    "profiles", "windows", "microsoft", "programdata", "program files",
    "program files (x86)", "common", "shared", "documents", "desktop",
    "downloads", "application", "applications", "bin", "lib", "share",
    "database", "databases", "storage", "state", "logs", "backup", "files",
}

_MIN_APP_TOKEN = 4


def _app_name_head(app_name: str) -> str:
    """The identifying first word of an application's registry name.

    "LocalSend version 1.17.0" → "localsend"; "PyCharm Community Edition
    2024.1" → "pycharm". Registry names carry editions and versions that never
    appear in a path, so matching the whole string finds nothing and matching
    any substring of it finds everything.
    """
    head = app_name.strip().lower().split()[0] if app_name.strip() else ""
    return head.strip("-_.()[]")


def _find_related_app(db_path: str, db_name: str, installed_apps: dict) -> str:
    """Name the app a detached database belongs to — only when it is provable.

    A database nobody can attribute is fine; one attributed to the *wrong*
    application is worse than one attributed to none, because the label is what
    the user acts on. Measured on a real profile, the old rule accepted a path
    segment that was merely a substring of an app's name, so the segment "Local"
    in AppData\\Local matched "LocalSend version 1.17.0" and Ollama's,
    FastStone's and NVIDIA's databases were each announced as LocalSend's. A
    Claude scratch folder was reported as "CPUID CPU-Z 2.17". Which app won even
    changed between runs, because the first match in registry order took it.

    Now a path segment has to *be* the app's name or start with it, generic
    structural segments are ignored, and the longest match wins so the answer no
    longer depends on dictionary order.

    installed_apps: dict returned by _get_installed_programs() — keys are
    normalised install paths, values are {'name': ..., 'publisher': ..., ...}
    """
    norm_db = db_path.replace("\\", "/").lower()
    parts = norm_db.split("/")[:-1]          # directories only, not the file
    # Drop the username: it is not evidence, and it can collide with an app.
    if len(parts) > 2 and parts[1] in _USER_CONTAINER_NAMES:
        parts = parts[:2] + parts[3:]
    segments = [p for p in parts
                if len(p) >= _MIN_APP_TOKEN and p not in _GENERIC_PATH_SEGMENTS]
    db_stem = os.path.splitext(db_name)[0].lower().strip("-_. ")

    matches = []
    for app_info in installed_apps.values():
        app_name = app_info.get("name", "") if isinstance(app_info, dict) else ""
        if not app_name:
            continue
        head = _app_name_head(app_name)
        if len(head) < _MIN_APP_TOKEN or head in _GENERIC_PATH_SEGMENTS:
            continue
        # A folder named after the app, or after the app plus a version suffix
        # ("PyCharmCE2024.1"). Never the other way round — that is the bug.
        if any(seg == head or seg.startswith(head) for seg in segments) \
                or (db_stem and db_stem.startswith(head)):
            matches.append((head, app_name))

    if not matches:
        return ""
    # Longest head first, then alphabetical. The tie-break matters: a vendor
    # shipping a dozen products (NVIDIA) would otherwise name the same folder
    # differently from one run to the next, on registry order alone.
    matches.sort(key=lambda m: (-len(m[0]), m[1]))
    return f"Likely database for {matches[0][1]}"


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
    """Detect the UWP/sandboxed app package container (AppData/Local/Packages)."""
    parts = norm_path.rstrip("/").split("/")
    return len(parts) >= 5 and parts[-3:] == ["appdata", "local", "packages"]


# The three fixed subdivisions of a Windows profile's AppData.
_APPDATA_ROOT_CHILDREN = {"local", "locallow", "roaming"}
# ...and containers one level below them that hold one folder PER APPLICATION.
# Local/Programs is where per-user installs land — the profile's own equivalent
# of Program Files. (Local/Packages has its own pass, _pass_appdata_packages.)
_APPDATA_APP_CONTAINERS = {("local", "programs")}


def _is_appdata_container_dir(norm_path: str) -> bool:
    """True for C:/Users/<user>/AppData and the per-app containers inside it.

    Matches AppData itself, its Local / LocalLow / Roaming children, and
    Local/Programs. These are pure structure: one folder per application,
    hundreds of them, spanning caches, saved logins, licences and game data all
    at once. Shown as a single entity, AppData is a 60 GB row the user cannot
    act on — deleting it is never the answer — and it double-counts every
    per-app entity detected underneath it. So they are claimed as structural
    nodes and never become entities themselves, exactly like C:/Users and
    AppData/Local/Packages.

    Depth-aware, so a stray "AppData" folder inside an application's own install
    tree is left alone and still classifies normally.
    """
    parts = norm_path.rstrip("/").split("/")
    if len(parts) < 4 or parts[1] not in _USER_CONTAINER_NAMES:
        return False
    if parts[3] != "appdata":
        return False
    if len(parts) == 4:
        return True
    if len(parts) == 5:
        return parts[4] in _APPDATA_ROOT_CHILDREN
    if len(parts) == 6:
        return (parts[4], parts[5]) in _APPDATA_APP_CONTAINERS
    return False


def _is_install_root_child(norm_path: str) -> bool:
    """True for the per-application folders inside an install root.

    Two shapes: C:/Program Files/<app> and C:/Users/<u>/AppData/Local/Programs/
    <app>, the per-user equivalent. Exact depth only, so components nested
    deeper stay owned by the application above them.
    """
    parts = norm_path.rstrip("/").split("/")
    if len(parts) == 3 and parts[1] in _INSTALL_ROOT_NAMES:
        return True
    return (len(parts) == 7
            and parts[1] in _USER_CONTAINER_NAMES
            and parts[3] == "appdata"
            and (parts[4], parts[5]) in _APPDATA_APP_CONTAINERS)


# ── UWP package-family-name → friendly app name ──────────────────
# A UWP per-app folder is named "<PackageName>_<PublisherId>", e.g.
# "SpotifyAB.SpotifyMusic_zpdnekdrzrea0". The publisher hash carries no
# meaning; the package name (before the final "_") identifies the app.
# Curated entries cover the popular apps; everything else is parsed.
_KNOWN_UWP_PACKAGES: dict = {
    "spotifyab.spotifymusic":            "Spotify",
    "microsoft.windowscalculator":       "Windows Calculator",
    "microsoft.windows.photos":          "Microsoft Photos",
    "microsoft.zunemusic":               "Groove Music",
    "microsoft.zunevideo":               "Films & TV",
    "microsoft.windowsstore":            "Microsoft Store",
    "microsoft.xboxapp":                 "Xbox",
    "microsoft.gamingapp":               "Xbox (Gaming App)",
    "microsoft.windowsterminal":         "Windows Terminal",
    "microsoft.screensketch":            "Snipping Tool",
    "microsoft.windowsnotepad":          "Notepad",
    "microsoft.paint":                   "Paint",
    "microsoft.windowssoundrecorder":    "Sound Recorder",
    "microsoft.windowscamera":           "Camera",
    "microsoft.windowsmaps":             "Maps",
    "microsoft.microsoftstickynotes":    "Sticky Notes",
    "microsoft.todos":                   "Microsoft To Do",
    "microsoft.outlookforwindows":       "Outlook (new)",
    "microsoft.windowsalarms":           "Clock",
    "microsoft.bing":                    "Bing",
    "microsoft.microsoftedge.stable":    "Microsoft Edge",
    "microsoft.yourphone":               "Phone Link",
    "9e2f88e3.twitter":                  "Twitter / X",
    "5319275a.whatsappdesktop":          "WhatsApp",
    "telegrammessengerllp.telegramdesktop": "Telegram",
    "discordinc.discord":                "Discord",
}


def _split_camel(text: str) -> str:
    """Insert spaces at camelCase and letter→digit boundaries.

    "WindowsCalculator" → "Windows Calculator"; "Photos" → "Photos".
    """
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    text = re.sub(r"(?<=[A-Za-z])(?=[0-9])", " ", text)
    return " ".join(text.split())


def _humanize_package_name(folder_name: str) -> str:
    """Map a UWP package folder name to a readable app name.

    "SpotifyAB.SpotifyMusic_zpdnekdrzrea0"        → "Spotify"
    "Microsoft.WindowsCalculator_8wekyb3d8bbwe"   → "Windows Calculator"
    "Microsoft.549981C3F5F10_8wekyb3d8bbwe"       → "Microsoft.549981C3F5F10"
    """
    base = folder_name.rsplit("_", 1)[0] if "_" in folder_name else folder_name
    known = _KNOWN_UWP_PACKAGES.get(base.lower())
    if known:
        return known

    segments = base.split(".")
    app_part = segments[-1] if segments else base
    pretty = _split_camel(app_part)
    # A segment that is mostly an ID (e.g. "549981C3F5F10") is useless as a
    # name — fall back to the publisher-qualified raw base so it stays
    # identifiable rather than rendering as garbage digits.
    letters = sum(c.isalpha() for c in app_part)
    digits = sum(c.isdigit() for c in app_part)
    if letters < 3 or len(pretty) < 3 or (digits >= 4 and digits >= letters):
        return base
    return pretty


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


# Folder names that mean "game saves" ONLY with corroborating context. "saves"
# is an ordinary English word: it labelled C:/Users/Nazar/LLaMA-Factory/saves —
# 417 MB of LLM fine-tuning checkpoints — as Game Save Data, and that single
# folder was 95% of the entire Saves category by size, so the category was both
# wrong and misleadingly weighted.
#
# "saved games" is deliberately absent: it is a Windows known folder, so the
# name IS the evidence.
_GENERIC_SAVE_DIR_NAMES = {"saves", "save", "sav", "userdata"}

# Path segments that corroborate a game. Any one of them is enough.
_GAME_CONTEXT_SEGMENTS = {
    "my games", "mygames", "saved games", "savedgames",
    "steamapps", "steam", "epic games", "epicgames", "gog galaxy", "gog",
    "origin games", "ea games", "ubisoft", "battle.net", "riot games",
    "rockstar games", "bethesda", "square enix", "xboxgames",
}


def _is_contextless_save_dir(lower_name: str, norm_path: str) -> bool:
    """True for a generically-named save folder with nothing to back it up."""
    if lower_name not in _GENERIC_SAVE_DIR_NAMES:
        return False
    segments = set(norm_path.rstrip("/").split("/"))
    return not (segments & _GAME_CONTEXT_SEGMENTS)


# Types that must never outrank "this is an installed program". A folder
# sitting directly inside an install root IS an application, whatever its
# contents happen to look like — content classification decides the identity of
# the whole folder from an extension ratio, and two .vhdx images are 68% of the
# bytes in C:/Program Files/WSL, so 621 files of DLLs and resources were
# labelled "Virtual Machine Storage" and filed under Virtual Machines.
#
# Deliberately not exhaustive: cache/log/dev types are never install-root
# children (they sit deeper), and a game installed to Program Files really is
# a game, so both are left to classify normally.
_NEVER_OUTRANKS_INSTALL_ROOT = {
    "installer", "installer_group",
    "vm_storage", "unknown_folder", "mixed_folder",
    "media_collection", "photo_collection", "video_collection",
    "audio_collection", "creative_project",
    "archive_group", "backup_group", "dataset",
    "document_folder", "database", "ai_models",
}


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
    # An install root's per-app folder is the installed program itself. Reading
    # its executables as "installers" made Podbye offer to recycle the program
    # directory of Microsoft OneDrive and Ollama.
    if entity_type in _NEVER_OUTRANKS_INSTALL_ROOT \
            and _is_install_root_child(norm_path):
        return "application", ("Installed application — remove it through its own "
                               "uninstaller, not by deleting the folder")
    return entity_type, ""


# ── Heterogeneous user-root explosion ─────────────────────────────
# Multi-purpose dump folders that should be broken into per-subfolder
# entities rather than shown as one opaque blob.
_DOWNLOAD_ROOT_NAMES = {"downloads", "download"}


def _is_download_root(path: str) -> bool:
    """True when *path* is a Downloads folder (the user's or any other)."""
    return os.path.basename(path.replace("\\", "/").rstrip("/")).lower() \
        in _DOWNLOAD_ROOT_NAMES


# Folders where "where is it" is a more useful answer than "what is it", mapped
# to the category the user sees. A dump folder holds unrelated things by
# definition, so classifying its contents by type scatters one folder across the
# whole chip bar and no view ever shows the folder itself.
#
# Documents is deliberately NOT here: "Documents" is already a *type* category
# (document_folder — a folder of documents, anywhere on disk), so adding it as a
# location would merge two different meanings into one chip. That needs the type
# category renamed first, which is a separate call.
_ORIGIN_ROOT_NAMES = {
    "downloads": "Downloads",
    "download": "Downloads",
    "desktop": "Desktop",
}


def _origin_root_of(norm_path: str) -> tuple[str, str]:
    """The user dump folder containing *norm_path*, as (root, category label).

    Deliberately stricter than _is_download_root, which accepts any folder with
    the name. Only C:/Users/<user>/<folder> and one sitting at a drive root
    count — an application's internal "downloads" staging folder is not the
    user's Downloads, and sweeping its contents into that view would be worse
    than the fragmentation this fixes. Seven such folders exist on the reporting
    machine.

    Matches the root itself too. The loose-file buckets ("Loose archives in
    Downloads", "Misc files in Downloads") are rooted at the folder rather than
    inside it, and they are most of what was going astray.
    """
    parts = norm_path.rstrip("/").split("/")
    for i, part in enumerate(parts):
        label = _ORIGIN_ROOT_NAMES.get(part)
        if label is None:
            continue
        if i == 1:                                        # c:/downloads/…
            return "/".join(parts[:i + 1]), label
        if i == 3 and parts[1] in _USER_CONTAINER_NAMES:  # c:/users/<u>/desktop/…
            return "/".join(parts[:i + 1]), label
    return "", ""


def _download_root_of(norm_path: str) -> str:
    """The user's Downloads folder containing *norm_path*, or ''."""
    root, label = _origin_root_of(norm_path)
    return root if label == "Downloads" else ""


_MULTIPURPOSE_ROOT_NAMES = {
    "documents", "document", "my documents", "downloads", "download",
    "desktop", "personal",
}
# A root must have at least this many subfolders before exploding is worthwhile.
_EXPLODE_MIN_SUBDIRS = 4
# ...and span at least this many distinct content types to count as diverse.
_EXPLODE_MIN_DISTINCT_TYPES = 2


def _root_is_heterogeneous(
    subdir_types: list,
    name_is_multipurpose: bool,
    subdir_count: int,
) -> bool:
    """Decide whether a folder is a diverse multi-purpose root worth exploding.

    subdir_types: per-subfolder content classifications (str or None).
    A folder qualifies when its subfolders span >= 2 distinct content types,
    or it is a known dump folder (Documents/Downloads/...) with enough
    subfolders that one blended entity would just be noise.
    """
    meaningful = {t for t in subdir_types if t}
    if len(meaningful) >= _EXPLODE_MIN_DISTINCT_TYPES:
        return True
    if name_is_multipurpose and subdir_count >= _EXPLODE_MIN_SUBDIRS:
        return True
    return False


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

# Version-control directories. Their presence names the parent a project, so
# they are exempt from the generic depth guard below - see pass 1.
_VCS_DIR_NAMES = frozenset({".git", ".svn", ".hg"})

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


# Filename evidence that a .exe distributes software rather than being it.
# Word-boundary anchored: "OneDriveStandaloneUpdater.exe" must NOT match on
# "Updater" — camelCase inside a program name is not installer intent.
_INSTALLER_NAME_RE = re.compile(
    r'(?:^|[-_.\s(])(setup|install|installer|update[rs]?|patch|'
    r'redist|vcredist|webinstaller|bootstrapper)(?:$|[-_.\s)0-9])',
    re.IGNORECASE,
)
# How downloaded installers are published: vlc-3.0.20-win64.exe,
# python-3.11.5-amd64.exe, node-v20.11.0-x64.exe.
_INSTALLER_VERSION_RE = re.compile(r'[-_]v?\d+[._]\d+', re.IGNORECASE)
_INSTALLER_ARCH_RE = re.compile(
    r'[-_.](x64|x86|amd64|arm64|win32|win64|windows)$', re.IGNORECASE)


def _looks_like_installer_file(filename: str, ext: str = "") -> bool:
    """True when a file installs software, rather than merely being executable.

    The distinction the extension cannot make. Measured on a real machine,
    treating every .exe as an installer classified C:/Program Files/Microsoft
    OneDrive (3 of 5 files .exe) and AppData/Local/Programs/Ollama (3 of 6) as
    installer collections — recycle-able, at Optional risk. Both are installed
    software, and neither carries any installer evidence in its filenames.
    """
    e = (ext or os.path.splitext(filename)[1]).lower()
    if e in _INSTALLER_PACKAGE_EXTS:
        return True
    if e != ".exe":
        return False
    stem = os.path.splitext(os.path.basename(filename))[0]
    return bool(_INSTALLER_NAME_RE.search(stem)
                or _INSTALLER_VERSION_RE.search(stem)
                or _INSTALLER_ARCH_RE.search(stem))


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
        return "build_folder", Reason("Build artifact folder name")

    if lname in _CACHE_KEYWORDS or lname.endswith("cache"):
        return "cache_folder", Reason("Cache folder name")

    if lname in {"tmp", "temp", "temporary"} or lname.endswith("tmp"):
        return "temp_folder", Reason("Temporary folder name")

    if lname in {"log", "logs"} or any(k in lname for k in ("log", "diag", "trace", "dump", "crash")):
        return "log_folder", Reason("Log/diagnostic folder name")

    if lname in _DEV_ASSET_DIR_NAMES:
        return "dev_artifacts", "Development/test asset folder name"

    if lname in _CONFIG_DIR_NAMES:
        if path_parts & _APPLICATION_SUPPORT_SEGMENTS:
            return "application_data", "Application configuration/support data"
        return "dev_artifacts", "Configuration folder"

    if path_parts & _APPLICATION_SUPPORT_SEGMENTS:
        return "application_data", Reason("Application support data path")

    if direct_file_names & _PROJECT_MARKER_FILES:
        return "dev_project", "Development project marker file"

    if direct_exts & _PROJECT_MARKER_EXTS:
        return "dev_project", "Development project file"

    if direct_dir_names & {".git", ".hg", ".svn", "src", "source", "tests", "test"}:
        return "dev_project", "Development project folder structure"

    if direct_exts and direct_exts.issubset(_LOG_EXTS):
        return "log_folder", "Log files"

    return None, ""


# Substrings that merely *appear* in a folder's name. Split out of
# _last_chance_folder_classification, whose other rules are exact names,
# marker files or path structure — evidence of a different order.
#
# "Ivankiv060626-test" is a survey flight. It contains "test", so it was
# classified dev_artifacts, which carries risk Safe: 53 GB of a photogrammetry
# engineer's aerial imagery, presented as build output that is safe to delete.
# A word in a name cannot outrank what is in the folder.
_WEAK_NAME_FOLDER_TYPES = (
    (("project", "workspace", "repo", "repository"), "dev_project",
     "Project/workspace folder name"),
    (("fixture", "test", "mock", "stub", "snapshot"), "dev_artifacts",
     "Development/test folder name"),
    (("config", "settings", "prefs", "profile"), "application_data",
     "Configuration/support folder name"),
)


def _weak_name_folder_type(lower_name: str) -> tuple:
    """(entity_type, reason) suggested by a word inside the folder name."""
    for keywords, etype, reason in _WEAK_NAME_FOLDER_TYPES:
        if any(k in lower_name for k in keywords):
            return etype, reason
    return None, ""


# How many children one folder contributes before _DetectionContext.sample()
# moves on to the next one.
_SAMPLE_SLICE = 25


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

        # Exact per-directory aggregates, computed once. sample() is capped and
        # must NEVER be used for sizing — these stats are the only source of
        # truth for entity size / file_count / folder_count.
        self.subtree_stats: dict[str, tuple] = self._build_subtree_stats()

        # Paths of entities whose size represents a whole subtree. Only these
        # take part in the disjointness correction; loose-file buckets and
        # installer groups stand for a filtered file set, not a subtree.
        self.subtree_entity_paths: set[str] = set()

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

    # ── exact aggregates ───────────────────────────────────────────
    def _build_subtree_stats(self) -> dict[str, tuple]:
        """(size, files, folders, max_mtime, max_atime) for every directory.

        Processed deepest-first so a parent always reads finished child totals.
        One O(n) pass, replacing the old per-entity partial walks that silently
        truncated at sample()'s item limit and under-reported every folder with
        more than `limit` descendants.
        """
        dir_norms = set(self.children_index.keys())
        for children in self.children_index.values():
            for c in children:
                if c.is_dir:
                    dir_norms.add(c.path.replace("\\", "/").lower())

        stats: dict[str, tuple] = {}
        for d in sorted(dir_norms, key=lambda p: p.count("/"), reverse=True):
            size = files = folders = 0
            mtime = atime = 0.0
            for c in self.children_index.get(d, []):
                if c.is_dir:
                    folders += 1
                    cs = stats.get(c.path.replace("\\", "/").lower())
                    if cs:
                        size += cs[0]
                        files += cs[1]
                        folders += cs[2]
                        mtime = max(mtime, cs[3])
                        atime = max(atime, cs[4])
                else:
                    files += 1
                    size += c.size_bytes
                mtime = max(mtime, c.modified)
                atime = max(atime, c.accessed)
            stats[d] = (size, files, folders, mtime, atime)
        return stats

    def subtree(self, dir_norm: str) -> tuple:
        """Exact (size, files, folders, mtime, atime) for a directory subtree."""
        return self.subtree_stats.get(dir_norm, (0, 0, 0, 0.0, 0.0))

    def subtree_files(self, dir_norm: str):
        """Yield every file Finding under dir_norm — uncapped."""
        stack = [dir_norm]
        while stack:
            d = stack.pop()
            for c in self.children_index.get(d, []):
                if c.is_dir:
                    stack.append(c.path.replace("\\", "/").lower())
                else:
                    yield c

    # ── tree traversal over the prefix index ───────────────────────
    def sample(self, dir_norm: str, limit: int = 1000) -> list[Finding]:
        """Up to `limit` descendants under dir_norm, breadth-first.

        A truncated SAMPLE, for content classification and preview lists only.
        Never use it to compute a size or a count — use subtree() for that.

        The order matters because the sample is *evidence*. Depth-first spent
        the whole budget inside whichever subfolder happened to be last, so
        the label described one branch while the row reported the tree:
        E:/Forge/investigations came back "mostly code & config" from the
        scripts at its top, and 31 of its 38 GB are .tif and .dmap imagery
        further down.

        Plain breadth-first is not enough either — one folder of 900 files
        still exhausts the budget before its sibling is reached. Each folder
        hands over a slice at a time and goes back in the queue, so the sample
        widens across the tree before it deepens anywhere in it.
        """
        result: list[Finding] = []
        queue = deque([(dir_norm, 0)])
        while queue and len(result) < limit:
            d, start = queue.popleft()
            children = self.children_index.get(d, [])
            for child in children[start:start + _SAMPLE_SLICE]:
                if len(result) >= limit:
                    break
                result.append(child)
                if child.is_dir:
                    queue.append((child.path.replace("\\", "/").lower(), 0))
            if start + _SAMPLE_SLICE < len(children):
                queue.append((d, start + _SAMPLE_SLICE))
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
        _children = ctx.sample(_norm)
        _ent = _build_entity(ctx,
            _f.path, _display, _etype, _children,
            f"Known monolith distribution: {_f.name}",
        )
        _fc = ctx.claim(_norm)
        ctx.emit_entity(_ent, _fc)
        p1_roots_found += 1

    t_p1_elapsed = int((_time.time() - t_p1_start) * 1000)
    ctx.log(f"[smart] phase 1: discovery — found {p1_roots_found} entity "
            f"roots · {t_p1_elapsed}ms")


def _pass_self(ctx: "_DetectionContext"):
    """Podbye's own folders, claimed first and marked protected.

    Runs before every other pass so nothing else can classify them. Left to the
    generic passes, %APPDATA%/Podbye/sessions reads as ordinary app data and
    %LOCALAPPDATA%/Podbye/cache as a cache folder — Safe, recycle-able — which
    let Podbye offer to delete the session store holding the results on screen.

    The wording lives in the UI, not here: the entity carries is_self and the
    dashboard phrases it in the user's language.
    """
    from app.services.self_paths import self_roots

    roots = self_roots()
    if not roots:
        return
    ctx.log("[smart] pass 0a: protecting Podbye's own folders...")
    found = 0
    for f in ctx.all_dirs:
        norm = f.path.replace("\\", "/").lower().rstrip("/")
        if norm in ctx.claimed_paths or norm not in roots:
            continue
        children = ctx.sample(norm)
        is_data = norm in _norm_self_data_dirs()
        ent = _build_entity(
            ctx, f.path,
            "Podbye (app data)" if is_data else "Podbye",
            "protected_system", children,
            "Podbye's own data — settings, scan history and cached AI answers"
            if is_data else "Podbye itself — the app doing the cleaning",
        )
        ent.is_self = True
        fc = ctx.claim(norm)
        ctx.emit_entity(ent, fc)
        found += 1
    if found:
        ctx.log(f"[smart]   → protected {found} of Podbye's own folder(s)")


def _norm_self_data_dirs() -> tuple[str, ...]:
    from app.services.self_paths import data_dirs
    return data_dirs()


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

        children = ctx.sample(norm_path)
        if not children:
            continue
        ent = _build_entity(ctx, 
            f.path, "NVIDIA Update Cache", "installer_cache", children,
            "NVIDIA update/cache staging folder",
        )
        fc = ctx.claim(norm_path)
        ctx.emit_entity(ent, fc)
        pass_entities += 1
    if pass_entities:
        ctx.log(f"[smart]   → merged {pass_entities} update cache entities")


def _pass_appdata_packages(ctx: "_DetectionContext"):
    """Split AppData/Local/Packages into named per-app data entities.

    Without this, the whole Packages tree collapses into one opaque
    "Application Packages" blob. Here each direct child package folder becomes
    its own application_data entity with a human-readable name
    ("Spotify (app data)", "Microsoft Photos (app data)"), and the Packages
    container itself is claimed as a pass-through node (no blob entity).
    """
    ctx.log("[smart] pass 0b: mapping AppData/Local/Packages to named apps...")
    ctx.confidence = 0.8  # package-family-name parse
    pass_entities = 0
    for f in ctx.all_dirs:
        norm_path = f.path.replace("\\", "/").lower().rstrip("/")
        if norm_path in ctx.claimed_paths:
            continue
        if not _is_appdata_packages_path(norm_path):
            continue

        for child in ctx.children_index.get(norm_path, []):
            if not child.is_dir:
                continue
            c_norm = child.path.replace("\\", "/").lower().rstrip("/")
            if c_norm in ctx.claimed_paths:
                continue
            display = _humanize_package_name(child.name)
            children = ctx.sample(c_norm)
            ent = _build_entity(ctx, 
                child.path, f"{display} (app data)", "application_data", children,
                f"Sandboxed app data for {display}",
            )
            fc = ctx.claim(c_norm)
            ctx.emit_entity(ent, fc)
            pass_entities += 1

        # Claim the container node itself (no entity) so pass 1 doesn't rebuild
        # the single Packages blob over the now-claimed children.
        ctx.claimed_paths.add(norm_path)

    if pass_entities:
        ctx.log(f"[smart]   → mapped {pass_entities} sandboxed app-data entities")


def _pass_game_save_engines(ctx: "_DetectionContext"):
    """Per-game save engines (Ren'Py, LOVE, ...) -> one game_saves entity per game.

    These engines drop one folder per game directly under an engine folder
    (e.g. RenPy/<Game>-<buildid>). Without this each game's folder becomes a
    "Misc files" blob, so a machine with years of played games reads as noise.
    The owning-game name, install status and creation date are filled in later
    by _enrich_game_saves. Engine-agnostic: driven by _PER_GAME_SAVE_ENGINES.
    """
    ctx.log("[smart] pass 0c: mapping per-game save engines...")
    pass_entities = 0
    for f in ctx.all_dirs:
        norm = f.path.replace("\\", "/").lower().rstrip("/")
        if norm in ctx.claimed_paths:
            continue
        if os.path.basename(norm) not in _PER_GAME_SAVE_ENGINES:
            continue
        for child in ctx.children_index.get(norm, []):
            if not child.is_dir:
                continue
            c_norm = child.path.replace("\\", "/").lower().rstrip("/")
            if c_norm in ctx.claimed_paths:
                continue
            game = _clean_game_name(child.name)
            children = ctx.sample(c_norm)
            ent = _build_entity(ctx,
                child.path, game, "game_saves", children,
                f"Save data for {game}",
            )
            fc = ctx.claim(c_norm)
            ctx.emit_entity(ent, fc)
            pass_entities += 1
        ctx.claimed_paths.add(norm)  # engine container node, pass-through

    if pass_entities:
        ctx.log(f"[smart]   mapped {pass_entities} per-game save folder(s)")


def _pass_downloads(ctx: "_DetectionContext"):
    """Downloads is a collection of individual downloads — never one blob.

    Each direct child FOLDER is a single downloaded item (an extracted archive,
    an installer's payload, a cloned repo) and is claimed whole. Without this
    the folder is treated like any other tree and one download shatters into
    unrelated fragments — a single extracted Qt build produced "Misc files in
    release", ".cache", "Misc files in translations", "Misc files in QtQml" and
    more, none of which means anything on its own.

    The Downloads folder itself is then claimed as a pass-through node (no
    entity of its own) so its loose files still bucket individually instead of
    collapsing into one "Downloads" blob.
    """
    ctx.log("[smart] pre-pass: mapping Downloads to individual items...")
    items = 0
    for f in ctx.all_dirs:
        norm = f.path.replace("\\", "/").lower().rstrip("/")
        if norm in ctx.claimed_paths or not _is_download_root(f.path):
            continue

        for child in ctx.children_index.get(norm, []):
            if not child.is_dir:
                continue
            c_norm = child.path.replace("\\", "/").lower().rstrip("/")
            if c_norm in ctx.claimed_paths:
                continue
            ent = _build_entity(
                ctx, child.path, child.name, "download_item",
                ctx.sample(c_norm), Reason("Downloaded item — kept whole"),
            )
            fc = ctx.claim(c_norm)
            ctx.emit_entity(ent, fc)
            items += 1

        # Pass-through claim: no blob entity for Downloads itself, so its loose
        # files fall through to the per-type/per-folder bucketer.
        ctx.claimed_paths.add(norm)

    if items:
        ctx.log(f"[smart]   → mapped {items} downloaded item(s)")


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
        # "saves" is a folder name, not evidence of a game. Skipping here lets
        # content classification have its say instead.
        if _is_contextless_save_dir(lower_name, norm_path):
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
            # Guard 4: too many levels from scan root for a generic entity.
            #
            # A version-control directory is exempt. This guard exists to stop
            # *generic* folder names ("cache", "logs", "bin") from producing an
            # entity wherever they happen to appear; .git is not generic, it is
            # the definitive statement that its parent is a project, and how
            # far down that sits is an accident of where someone keeps code.
            #
            # Measured on this machine: seven repositories under
            # E:/Work/Projects/Focus/irizi-eyesight sit at rel_depth 6 and were
            # all suppressed here. Only the one carrying requirements.txt was
            # detected at all - by the marker pass, not this one - so a folder
            # of seven projects showed one item in its drill-down while its
            # size counted all seven.
            #
            # Guards 1-3 still apply: a checkout inside a registered
            # application, deep inside Program Files, or under a framework
            # container is still not a project of the user's.
            if lower_name not in _VCS_DIR_NAMES:
                rel_depth = norm_path.count("/") - root_depth
                if rel_depth > _MAX_GENERIC_DIR_DEPTH:
                    continue

        # For VCS dirs (.git, .svn, .hg), the entity is the parent project.
        if lower_name in _VCS_DIR_NAMES:
            parent = f.parent
            parent_norm = parent.replace("\\", "/").lower()
            if parent_norm in ctx.claimed_paths:
                continue
            children = ctx.sample(parent_norm)
            ent = _build_entity(ctx, parent, os.path.basename(parent),
                                "dev_project", children,
                                f"Contains {lower_name} directory")
            ctx.claim(parent_norm)
            ctx.emit_entity(ent)
            pass_entities += 1
            continue

        children = ctx.sample(norm_path)

        # ── Content gate: don't trust a content-folder NAME unless the
        # folder's actual files back it up. A "Videos" folder with no videos
        # is left for the content/sweep passes instead of being mislabelled.
        if etype in _CONTENT_TYPE_EXTS and not _content_confirms_type(children, etype):
            continue

        # ── Asset gate: an image folder that is really app/UI content
        # (icons, sprites, web graphics) is app data, not a photo library.
        if etype in _USER_IMAGE_MEDIA_TYPES and _looks_like_app_assets(
                norm_path, f.name, [c for c in children if not c.is_dir]):
            etype = "application_data"

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
            # Don't qualify with the account name (parent is the user home dir).
            parent_is_username = _is_user_home_dir(f.parent.replace("\\", "/").lower())
            if etype == "development_environment":
                display = label
            elif (parent_name
                    and parent_lower not in _QUALIFIER_SKIP_SEGS
                    and parent_lower not in _GENERIC_FOLDER_NAMES
                    and not parent_is_username):
                display = f"{label} – {parent_name}"
            else:
                display = label
        elif etype == "application_data":
            display = ("Application Packages" if _is_appdata_packages_path(norm_path)
                       else _qualify_folder_name(f.name, f.path))
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

        ent = _build_entity(ctx, 
            f.path, display, etype, children,
            corrected_reason or Reason("Known directory: {name}", name=f.name),
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

            children = ctx.sample(entity_norm)
            ent = _build_entity(ctx, 
                entity_path, entity_name, "application", children,
                f"Installed application from registry: "
                f"{app_info.get('publisher', 'Unknown')}",
                app_version=app_info.get("version", ""),
                app_publisher=app_info.get("publisher", ""),
                install_date=app_info.get("install_date", ""),
                uninstall_string=app_info.get("uninstall_string", ""),
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

            children = ctx.sample(parent_norm)
            if nvidia_cache_norm:
                entity_type = "installer_cache"
                marker_reason = Reason("NVIDIA update/cache staging folder")
            elif marker_type == "application":
                entity_type = "portable_app"
                marker_reason = Reason("Portable application marker: {name}", name=f.name)
            else:
                entity_type = marker_type
                marker_reason = Reason("Project/application marker: {name}", name=f.name)

            ent = _build_entity(ctx, parent, display_name, entity_type, children,
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

            children = ctx.sample(parent_norm)
            ent = _build_entity(ctx, parent, display_name, etype, children,
                                Reason("Database file: {name}", name=f.name))
            ctx.claim(parent_norm)
            ctx.emit_entity(ent)
            pass_entities_portable += 1

    ctx.log(f"[smart]   → scanned {files_processed:,} files · created "
            f"{pass_entities_portable} portable app entities · total "
            f"entities: {ctx.entities_created}")


# Folder names inside a browser profile that hold regenerable cached data.
# Deliberately precise: "Service Worker" as a whole is NOT here — it also holds
# the registration Database — so only its two cache children are listed.
_BROWSER_CACHE_DIRS = {
    "cache", "code cache", "gpucache", "shadercache", "grshadercache",
    "dawngraphitecache", "dawnwebgpucache", "media cache",
    "cachestorage", "scriptcache",
}

# ── Electron code cache ──────────────────────────────────────────
# Every Electron app embeds Chromium, so every one of them keeps a folder
# literally named "chrome" at CachedData/<build-hash>/chrome — V8's compiled-JS
# cache, keyed by app build. Reported from a real scan: ten of these under
# Windsurf and seven under VS Code, each announced as "Chrome Data" in Browser
# Data with the promise that passwords and bookmarks were untouched. They hold
# no browsing data at all, and the path is unreadable:
#   AppData/Roaming/Windsurf/CachedData/abcd9c86…/chrome
# One row per app, named after the app that owns it, says what it is.
_ELECTRON_CACHE_DIR = "cacheddata"

# Folder name → product name, where they differ. Anything else uses its folder
# name, which is already the product name for Windsurf, Cursor, Slack, …
_ELECTRON_APP_NAMES = {
    "code": "VS Code",
    "code - insiders": "VS Code Insiders",
    "code - oss": "VS Code OSS",
    "vscodium": "VSCodium",
}


def _pass_electron_code_cache(ctx: "_DetectionContext"):
    """Collapse an Electron app's per-build code cache into one entity."""
    ctx.log("[smart] pre-pass: collapsing Electron code caches...")
    found = 0
    for f in ctx.all_dirs:
        norm = f.path.replace("\\", "/").lower().rstrip("/")
        if norm in ctx.claimed_paths or f.name.lower() != _ELECTRON_CACHE_DIR:
            continue
        # Build folders are content-addressed hashes. Requiring them keeps a
        # folder that merely happens to be called "CachedData" out of this.
        builds = [c for c in ctx.children_index.get(norm, [])
                  if c.is_dir and _HEX_BLOB_RE.match(c.name)]
        if not builds:
            continue

        owner = os.path.basename(os.path.dirname(norm))
        app = _ELECTRON_APP_NAMES.get(owner, "") or _qualify_folder_name(
            os.path.basename(os.path.dirname(f.path.replace("\\", "/"))), f.path)
        ent = _build_entity(
            ctx, f.path, f"{app} · code cache", "cache_folder", ctx.sample(norm),
            f"Compiled-code cache for {app}, one copy per version it has run "
            f"({len(builds)} kept, only the current one is used). {app} rebuilds "
            "it on the next start — this is not browsing data.",
        )
        fc = ctx.claim(norm)
        ctx.emit_entity(ent, fc)
        found += 1
    if found:
        ctx.log(f"[smart]   → collapsed {found} Electron code cache(s)")


def _pass_browser_caches(ctx: "_DetectionContext"):
    """Split regenerable caches out of browser profiles.

    A browser profile is mostly irreplaceable — passwords, cookies, history,
    bookmarks — so the whole tree sits at Review and its cache goes unnoticed
    inside it. Measured on a real profile: 794 MB of the 6.35 GB Chrome folder
    is pure cache, 613 MB of it under Service Worker alone.

    Runs before the profile pass so the caches are claimed first and the profile
    keeps everything else. Only the cache children of Service Worker are taken;
    its Database holds registrations and is left alone.
    """
    ctx.log("[smart] pre-pass: separating browser caches from profile data...")
    found = 0
    for f in ctx.all_dirs:
        norm = f.path.replace("\\", "/").lower().rstrip("/")
        if norm in ctx.claimed_paths:
            continue
        if f.name.lower() not in _BROWSER_CACHE_DIRS:
            continue
        # Must actually sit inside a browser's storage. Requiring the profile
        # container alone was not enough: any path containing "profiles" matched,
        # so a project folder like .../profiles/Cache was announced as browser
        # cache and told the user their "passwords, cookies, history and
        # bookmarks are untouched" — a statement about data that folder does not
        # hold. The browser itself must be named in the path.
        browser_key = next((b for b in _BROWSER_KEYWORDS if b in norm), None)
        if browser_key is None:
            continue
        if "user data" not in norm and "profiles" not in norm:
            continue
        browser = browser_key.title()
        # Name by location, not just the folder name: a profile holds several
        # folders literally called "Cache", and leaving them identical makes the
        # disambiguator append the same word again ("Cache (Cache)").
        parent = os.path.basename(os.path.dirname(f.path.replace("\\", "/")))
        where = f"{parent} / {f.name}" if parent else f.name
        ent = _build_entity(
            ctx, f.path, f"{browser} cache · {where}", "cache_folder",
            ctx.sample(norm),
            Reason("Cached web content — {browser} rebuilds it as you browse; "
                   "your passwords, cookies, history and bookmarks are untouched",
                   browser=browser),
        )
        fc = ctx.claim(norm)
        ctx.emit_entity(ent, fc)
        found += 1
    if found:
        ctx.log(f"[smart]   → separated {found} browser cache folder(s)")


# The containers a browser creates for its profiles, and the files it keeps
# inside one. Either is proof; the browser's *name* is not.
_BROWSER_PROFILE_CONTAINERS = {"user data", "profiles"}
_BROWSER_PROFILE_MARKERS = {
    "preferences", "secure preferences", "local state", "bookmarks",
    "cookies", "history", "login data", "web data", "favicons",
    "places.sqlite", "prefs.js", "logins.json", "key4.db", "cert9.db",
}


def _browser_from_path(norm_path: str) -> str:
    """The browser named by a whole segment of *norm_path*, or ''.

    Segment-exact, never substring. "EdgeJourneys" and "EdgeEDrop" (Copilot's
    own data folders) and "chrome-extension_…​.indexeddb.leveldb" (one extension's
    IndexedDB store) all contain a browser's name without being one, and each
    was listed as its own 0.0 MB "Edge Data" / "Chrome Data" row.

    Deepest segment wins, so .../Google/Chrome/User Data reports Chrome.
    """
    for part in reversed(norm_path.rstrip("/").split("/")):
        if part in _BROWSER_KEYWORDS:
            return part
    return ""


def _has_browser_profile_evidence(norm_path: str, direct_children: list) -> bool:
    """Does this folder actually hold a browser profile?

    Requiring only a browser keyword in the path was not enough. Every Electron
    app has a folder named "chrome" (see _pass_electron_code_cache), and the
    context gate accepted a bare "appdata" or "roaming" anywhere in the path —
    so AppData/Roaming/Windsurf/CachedData/<hash>/chrome was reported as
    "Chrome Data" at Review, telling the user their passwords, cookies, history
    and bookmarks lived there. They did not.

    Three ways to prove it: the folder sits inside a profile container, it holds
    one, or its own files are the ones a browser writes into a profile.
    """
    p = norm_path.rstrip("/") + "/"
    if any(f"/{c}/" in p for c in _BROWSER_PROFILE_CONTAINERS):
        return True
    names = {c.name.lower() for c in direct_children}
    if names & _BROWSER_PROFILE_CONTAINERS:
        return True
    return bool(names & _BROWSER_PROFILE_MARKERS)


def _pass3_browser_profiles(ctx: "_DetectionContext"):
    """Pass 3 - browser profile/data folders, by profile evidence + naming."""
    ctx.log("[smart] pass 3: detecting browser profiles...")
    ctx.confidence = 0.8  # browser name + profile-storage evidence
    pass_entities = 0
    # Shallowest first, exactly as pass 1 does: a profile tree must be claimed
    # by its root before its insides can become entities of their own. In scan
    # order, leaves won — "Copilot/User Data/Default/EdgeJourneys" and
    # "…/EdgeEDrop" were listed as separate 0.0 MB "Edge Data" rows instead of
    # staying inside the one profile they belong to.
    for f in sorted(ctx.all_dirs,
                    key=lambda d: d.path.replace("\\", "/").count("/")):
        ctx.processed_candidates += 1
        norm_path = f.path.replace("\\", "/").lower()
        if norm_path in ctx.claimed_paths:
            continue

        lower_name = f.name.lower()
        browser = _browser_from_path(norm_path)
        if browser and _has_browser_profile_evidence(
                norm_path, ctx.children_index.get(norm_path.rstrip("/"), [])):
            children = ctx.sample(norm_path)
            if children:
                browser_label = browser.title()
                ent = _build_entity(ctx, f.path, f"{browser_label} Data",
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
            children = ctx.sample(norm_path)
            if children:
                ent = _build_entity(ctx, f.path, f.name, "game", children,
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
        exact = _named_exactly_like_cache(lower_name)
        if not exact and not _has_cache_word(lower_name):
            continue

        # A directory inside a package container is a package, whatever it is
        # called: six copies of the npm package "http-cache-semantics" were
        # claimed as caches marked Safe on a real machine. A folder actually
        # *named* "cache" inside one still counts — only the widened name test
        # is gated, so the long-standing behaviour is untouched.
        if not exact and _inside_package_container(norm_path):
            continue

        children = ctx.sample(norm_path)
        # And a name that merely mentions a cache has to be backed by content
        # that is not obviously something else.
        if not exact and _looks_like_source_tree(children):
            continue

        source_app = _infer_cache_source(norm_path)
        display_name = f"Cache for {source_app}" if source_app else f.name
        ent = _build_entity(ctx,
            f.path, display_name, "cache_folder", children,
            (Reason("Cache folder for {app}", app=source_app) if source_app
             else Reason("Cache folder")),
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
            children = ctx.sample(norm_path)
            ent = _build_entity(ctx, f.path, f.name, "protected_system", children,
                                Reason("System or protected path detected"))
            fc = ctx.claim(norm_path)
            ctx.emit_entity(ent, fc)
            pass_entities += 1
        elif _is_container_path(norm_path):
            # Container paths (Program Files) should NOT become System
            # entities if they contain meaningful child entities.
            children = ctx.sample(norm_path)
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
                    ent = _build_entity(ctx, f.path, f.name, "portable_app", children,
                                        "Installed application (Program Files)")
                    fc = ctx.claim(norm_path)
                    ctx.emit_entity(ent, fc)
                    pass_entities += 1

    ctx.coverage_progress("protected_paths")
    ctx.log(f"[smart]   → created {pass_entities} protected/system "
            f"entities · skipped {skipped_containers} containers "
            f"· total: {ctx.entities_created}")


def _pass_explode_user_roots(ctx: "_DetectionContext"):
    """Pre-pass - break diverse multi-purpose roots into per-subfolder entities.

    A folder like Documents or Downloads full of unrelated subfolders would
    otherwise be swept into one giant blended entity (a "wall of noise"). When
    such a root is heterogeneous, claim ONLY the root node (no entity) so its
    subfolders become individual candidates for the later classification passes
    and its loose files fall through to the loose-file bucketer (the
    "stragglers"). Runs before pass 1 so generic names such as
    "documents"/"downloads" are not claimed as a single blob first.
    """
    ctx.log("[smart] pre-pass: exploding heterogeneous user roots...")
    exploded = 0
    candidates = [
        f for f in ctx.all_dirs
        if f.path.replace("\\", "/").lower().rstrip("/") not in ctx.claimed_paths
        and (
            f.parent.replace("\\", "/").lower().rstrip("/") in ctx.claimed_paths
            or f.parent.replace("\\", "/").lower().rstrip("/") == ctx.root_norm
            or f.name.lower() in _MULTIPURPOSE_ROOT_NAMES
        )
    ]
    candidates.sort(key=lambda d: d.path.replace("\\", "/").count("/"))

    for d in candidates:
        norm = d.path.replace("\\", "/").lower().rstrip("/")
        if norm in ctx.claimed_paths:
            continue
        # The user home dir (C:/Users/Nazar) is the ultimate diverse root and
        # must never be shown as one deletable "User Profile" blob — explode it.
        is_home = _is_user_home_dir(norm)
        if (norm == ctx.root_norm or _is_drive_root(norm)
                or _is_user_container_dir(norm)):
            continue
        # Never explode protected, sandboxed, or app-owned trees.
        if _is_protected_path(norm) or _is_appdata_packages_path(norm):
            continue
        # A directory that IS a registered install root (e.g. C:/Qt, whose
        # registry entry covers the whole tree) must stay whole — pass 2 will
        # claim it as one application. Exploding it here strands the root and
        # lets each version/tool subfolder fragment into its own "Qt (…)".
        if norm in ctx.installed_apps or (norm + "/") in ctx.installed_apps:
            continue
        # Same reasoning for software installed outside Program Files that is
        # not registered by path: a drive-root folder naming a present
        # application IS that install, and exploding it scatters the app across
        # its own components — PyCharm losing its bundled JRE to a stray "jbr".
        # strong_only: a PATH entry or a running process sharing a name is not
        # enough to hide a diverse folder's contents.
        if norm.count("/") == 1 and _app_presence(
                os.path.basename(norm), strong_only=True)[0] == _PRESENT:
            continue
        subdirs = [
            c for c in ctx.gather_direct(norm)
            if c.is_dir
            and c.path.replace("\\", "/").lower().rstrip("/") not in ctx.claimed_paths
        ]
        if len(subdirs) < _EXPLODE_MIN_SUBDIRS:
            continue

        # A user home dir counts as multi-purpose by definition, so it explodes
        # whenever it has enough subfolders.
        name_mp = is_home or d.name.lower() in _MULTIPURPOSE_ROOT_NAMES
        subdir_types = [
            _classify_by_content(ctx.sample(sd.path.replace("\\", "/").lower()))
            for sd in subdirs
        ]
        if not _root_is_heterogeneous(subdir_types, name_mp, len(subdirs)):
            continue

        # Pass-through claim: only the node, never its descendants.
        ctx.claimed_paths.add(norm)
        exploded += 1
        ctx.log(f"[smart]   → exploded '{d.name}' into {len(subdirs)} subfolders")

    if exploded:
        ctx.log(f"[smart]   → exploded {exploded} heterogeneous user root(s) "
                f"into per-subfolder entities")


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
        # A drive root is never one content collection. Pass 7 has always
        # skipped it; this pass did not, so D:/ — whose only loose file is a
        # RESULTS.md next to nine drone-survey folders — became a "Documents
        # Folder" carrying the whole drive's recursive totals.
        if _is_drive_root(norm_path) or norm_path.rstrip("/") == ctx.root_norm:
            continue

        direct = ctx.gather_direct(norm_path)
        direct_files = [c for c in direct if not c.is_dir]
        if not direct_files:
            continue

        # Judge the folder by files that are actually a share of it. The
        # entity reports RECURSIVE totals, so classifying from a handful of
        # loose files at the top labels 187,555 aerial photos "Documents"
        # because one report.md sits beside them. Same guard pass 7 uses.
        descendants = ctx.sample(norm_path)
        if _direct_files_describe_folder(direct_files, descendants):
            evidence, reason = direct_files, Reason("Content analysis")
        else:
            evidence = [c for c in descendants if not c.is_dir]
            reason = Reason("Content analysis of the files inside")
        if not evidence:
            continue

        etype = _classify_by_content(evidence)
        if etype:
            # App/UI asset folders look image-dominant but are not user media.
            if etype in _USER_IMAGE_MEDIA_TYPES and _looks_like_app_assets(
                    norm_path, d.name, evidence):
                etype = "application_data"
                reason = Reason("Application/UI assets, not a personal media library")
            display = _qualify_folder_name(d.name, d.path)
            ent = _build_entity(ctx, d.path, display, etype, direct, reason)
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
        descendants = ctx.sample(norm_path)
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

        # Only let the folder's own loose files name it when they are a real
        # share of it; otherwise the subfolders decide, further down.
        etype = None
        direct_files_speak = _direct_files_describe_folder(direct_files, descendants)
        if direct_files_speak:
            etype = _classify_by_content(direct_files)

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
            elif lower_name in _DIR_ENTITY_MAP \
                    and not _is_contextless_save_dir(lower_name, norm_path):
                etype = _DIR_ENTITY_MAP[lower_name]
                corrected_type, _ = _safety_correct_entity_type(d.path, etype)
                etype = corrected_type

        # Look at what is actually inside, before a word in the folder's name
        # is allowed to guess.
        #
        # Content classification only ever saw a folder's DIRECT files, so a
        # folder holding nothing but subfolders was handed an empty list and
        # fell through to Unknown. That is how people organise almost
        # everything — Videos/2024/, Music/Artist/Album/, one subfolder per
        # survey flight — and on a real scan it was 83 rows and 420 GB, the
        # largest category by size, none of it labelled.
        #
        # The structural and known-name rules above stay ahead of it:
        # "Application Support/WidgetApp/config.db" is application data
        # because of where it lives, not a database because of what is in it.
        # The *substring* guesses below no longer do, and that ordering was a
        # bug of its own: coordinate_recovery_outputs matched "output" and was
        # filed as a backup set, over 50 GB of aerial imagery.
        if etype in (None, "unknown_folder") and (not direct_files
                                                  or not direct_files_speak):
            descendant_files = [c for c in descendants if not c.is_dir]
            recursive_type = None
            if descendant_files:
                recursive_type = (_classify_by_content(descendant_files)
                                  or _plurality_content_type(descendant_files))
            if recursive_type:
                etype = recursive_type
                fallback_reason = Reason("Content analysis of the files inside")

        # A tree of source and configuration is a project, not an
        # unclassified pile.
        if etype in (None, "unknown_folder"):
            evidence = (direct_files if direct_files_speak
                        else [c for c in descendants if not c.is_dir])
            if _looks_like_source_tree(evidence):
                etype = "dev_project"
                fallback_reason = Reason("Source code and configuration files")

        # Only now, with nothing else to go on, read the name.
        if etype in (None, "unknown_folder"):
            weak_type, weak_reason = _weak_name_folder_type(lower_name)
            if weak_type:
                etype = weak_type
                fallback_reason = weak_reason
        if etype in (None, "unknown_folder"):
            keyword_type = _keyword_folder_type(lower_name)
            if keyword_type:
                etype = keyword_type
                fallback_reason = fallback_reason or "Name-based classification"
        if etype is None:
            etype = "unknown_folder"

        # Content gate: a media/document classification (whether from the name
        # map or the name keywords) must be backed by real files of that type.
        # Prevents name-only matches like "Windows Photo Viewer" → Images.
        if etype in _CONTENT_TYPE_EXTS and not _content_confirms_type(descendants, etype):
            etype = "unknown_folder"
            fallback_reason = "name suggested media, but the files did not confirm it"

        # Asset gate: an image folder that is really app/UI content (icons,
        # sprites, web graphics) is app data, not a personal photo library.
        if etype in _USER_IMAGE_MEDIA_TYPES and _looks_like_app_assets(
                norm_path, d.name, [c for c in descendants if not c.is_dir]):
            etype = "application_data"
            fallback_reason = "Application/UI assets, not a personal media library"

        corrected_type, corrected_reason = _safety_correct_entity_type(d.path, etype)
        if corrected_type != etype:
            etype = corrected_type

        reason = corrected_reason or fallback_reason or (
            "Name-based classification" if etype != "unknown_folder"
            else Reason("Ungrouped folder")
        )

        # ── Display name with ownership context ───────────────────────
        if etype == "user_profile":
            display = f"{d.name} Profile"
        elif etype == "application_data":
            display = ("Application Packages" if _is_appdata_packages_path(norm_path)
                       else _qualify_folder_name(d.name, d.path))
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
        elif etype in ("unknown_folder", "mixed_folder"):
            # Opaque folder: give the user a content hint instead of a bare
            # (possibly cryptic) folder name.
            display = _descriptive_folder_name(d.name, d.path, descendants)
            # "Unclassified" is not what we found. When the contents can be
            # described at all, the folder is several things rather than an
            # unknown thing, and saying so is both truer and less alarming on
            # a 36 GB row. Same category, same risk, same actions: the two
            # types differ only in the words they put on the row.
            if etype == "unknown_folder" and _content_descriptor(descendants):
                etype = "mixed_folder"
        else:
            display = _qualify_folder_name(d.name, d.path)

        # Name-classified folders are a weak signal; a bare unknown_folder
        # is weaker still.
        ctx.confidence = 0.4 if etype != "unknown_folder" else 0.2
        ent = _build_entity(ctx, d.path, display, etype, descendants, reason)
        fc = ctx.claim(norm_path)
        ctx.emit_entity(ent, fc)
        pass_entities += 1

        if _pass7_i % 25 == 0:
            ctx.coverage_progress("unknown_sweep")

    ctx.coverage_progress("unknown_sweep")
    ctx.log(f"[smart]   → created {pass_entities} unknown/mixed folder "
            f"entities · total: {ctx.entities_created}")


# ── Loose-file promotion policy ───────────────────────────────────
# The product rule is *not* "files over a megabyte are entities". It is:
# group items that share a lifecycle, and promote an item that is
# independently significant enough to deserve its own deletion decision.
#
# Size is the first cheap proxy for "independently significant", and the
# threshold is a policy knob rather than part of the model — measured on a
# real all-drives session, 37 type-grouped buckets held 288 files, of which
# 72% were under 100 KB with a median of 16 KB. Exploding all of them would
# have manufactured ~200 findings for things nobody adjudicates (18 config
# files totalling 0.2 MB), while the files that genuinely deserve a decision
# — a 412 MB archive, a 352 MB project export — stayed buried in a bucket.
#
# Future semantic rules (an installer, a dated export, anything Podbye can
# name) belong in deserves_own_finding, not in a second threshold.
STANDALONE_LOOSE_FILE_BYTES = 1024 * 1024


# Directories whose contents belong to something that manages them. A file in
# here is not an independent decision however big it is: you do not delete one
# blob out of Ollama's store by hand, you remove the model.
_MANAGED_STORE_SEGMENTS = frozenset(
    set(_AI_ML_PATH_KEYWORDS) | set(_PACKAGE_CONTAINERS) | {"blobs", "objects"}
)


def _inside_managed_store(path: str) -> bool:
    """True when *path* sits inside a store that owns its contents."""
    segments = [seg for seg in
                str(path or "").replace("\\", "/").lower().split("/")[:-1] if seg]
    return any(seg in _MANAGED_STORE_SEGMENTS for seg in segments)


def deserves_own_finding(finding) -> bool:
    """True when a loose file should be its own finding rather than bucketed.

    Two questions, in order. Does anything own it? Three 3 GB files under
    ``.ollama/models/blobs`` are one model store with one lifecycle, and
    splitting them into three findings offers a decision nobody can act on.
    Then: is it big enough to be worth deciding about on its own?
    """
    if _inside_managed_store(getattr(finding, "path", "")):
        return False
    return int(getattr(finding, "size_bytes", 0) or 0) >= STANDALONE_LOOSE_FILE_BYTES


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
        elif ext in _ARCHIVE_EXTS or _looks_like_installer_file(f.name, ext):
            _buckets["archive_group"].append(f)
        elif ext in _DATABASE_EXTS:
            _buckets["database"].append(f)
        elif _is_model_file(f):
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

    def _bucket_entity(bucket_type: str, dir_path: str, dir_files: list,
                       name: str) -> SmartEntity:
        """One loose-file bucket — or, when it holds one file, that file.

        "Loose AI model files in C:" was a row in the AI/ML category standing
        for exactly one 1 KB file, and it named neither the file nor anything
        else a person could act on: the row said C:/, the Information tab said
        C:/, and the Files tab did not even open itself for a single file. The
        user's words were "it does not show what is proposed to delete".

        A bucket of one has nothing to group, so it becomes the file: its
        name, its path, its size. This is what pass 8 already does for a
        loose installer, for the same reason.
        """
        if len(dir_files) == 1:
            only = dir_files[0]
            return SmartEntity(
                path=only.path,
                name=only.name,
                entity_type=bucket_type,
                size_bytes=only.size_bytes,
                file_count=1,
                folder_count=0,
                modified=only.modified,
                accessed=only.accessed,
                children_sample=[only.name],
                removable_file_paths=[only.path],
            )
        return SmartEntity(
            path=dir_path,
            name=name,
            entity_type=bucket_type,
            size_bytes=sum(f.size_bytes for f in dir_files),
            file_count=len(dir_files),
            folder_count=0,
            modified=max((f.modified for f in dir_files), default=0),
            accessed=max((f.accessed for f in dir_files), default=0),
            children_sample=[f.name for f in dir_files[:15]],
            removable_file_paths=[f.path for f in dir_files],
        )

    def _emit_single(bucket_type: str, one: Finding) -> int:
        """Emit one file as a finding in its own right."""
        ent = _bucket_entity(bucket_type, one.parent, [one], one.name)
        ctx.emit_entity(ent)
        ctx.claimed_paths.add(one.path.replace("\\", "/").lower())
        return 1

    def _emit_dir_split(bucket_type: str, files: list, label: str) -> int:
        """Emit one entity per parent directory, returning how many were made.

        EVERY loose bucket is split by folder, not just the misc catch-all.
        A single bucket rooted at the scan target reported the drive root as
        its path — "Loose documents" at "C:/" for files actually sitting on the
        Desktop. That hides where the files really are, and worse, it merges
        unrelated folders into one entity so a single click would recycle files
        from all over the disk.
        """
        emitted = 0
        promoted = [f for f in files if deserves_own_finding(f)]
        rest = [f for f in files if not deserves_own_finding(f)]
        for one in promoted:
            emitted += _emit_single(bucket_type, one)

        by_dir: dict[str, list[Finding]] = defaultdict(list)
        for f in rest:
            by_dir[f.parent].append(f)
        for dir_path, dir_files in sorted(
            by_dir.items(), key=lambda x: -sum(f.size_bytes for f in x[1])
        ):
            ent = _bucket_entity(
                bucket_type, dir_path, dir_files,
                f"{label} in {_dir_display_name(dir_path)}")
            ctx.emit_entity(ent)
            for f in dir_files:
                ctx.claimed_paths.add(f.path.replace("\\", "/").lower())
            emitted += 1
        return emitted

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
                          if _looks_like_installer_file(f.name, f.extension)]
            arch_files = [f for f in bucket_files
                          if not _looks_like_installer_file(f.name, f.extension)]

            for inst in inst_files:
                product = _installer_display_name(inst.name)
                # Point the entity at the actual installer FILE — not its parent
                # folder — so the UI shows the exact file and cleanup recycles
                # only that file (never the whole Downloads folder).
                ent = SmartEntity(
                    path=inst.path,
                    name=f"Installer ({product})",
                    entity_type="installer",
                    size_bytes=inst.size_bytes,
                    file_count=1,
                    folder_count=0,
                    modified=inst.modified,
                    accessed=inst.accessed,
                    children_sample=[inst.name],
                    removable_file_paths=[inst.path],
                )
                ctx.emit_entity(ent)
                ctx.claimed_paths.add(inst.path.replace("\\", "/").lower())
                pass8_entities += 1

            if arch_files:
                pass8_entities += _emit_dir_split(
                    "archive_group", arch_files, _BUCKET_LABELS["archive_group"])

        elif bucket_type == "mixed_folder":
            # Always split misc files by parent directory so the location
            # is visible rather than vanishing into one opaque bucket.
            for one in [f for f in bucket_files if deserves_own_finding(f)]:
                pass8_entities += _emit_single("mixed_folder", one)

            by_dir: dict[str, list[Finding]] = defaultdict(list)
            for f in bucket_files:
                if deserves_own_finding(f):
                    continue
                by_dir[f.parent].append(f)

            for dir_path, dir_files in sorted(
                by_dir.items(),
                key=lambda x: -sum(f.size_bytes for f in x[1]),
            ):
                dir_name = _dir_display_name(dir_path)
                desc = _content_descriptor(dir_files)
                misc_name = (f"Misc files in {dir_name} · {desc}" if desc
                             else f"Misc files in {dir_name}")
                ent = _bucket_entity("mixed_folder", dir_path, dir_files,
                                     misc_name)
                ctx.emit_entity(ent)
                for f in dir_files:
                    ctx.claimed_paths.add(f.path.replace("\\", "/").lower())
                pass8_entities += 1

        else:
            label = _BUCKET_LABELS.get(bucket_type, "Uncategorized files")
            pass8_entities += _emit_dir_split(bucket_type, bucket_files, label)

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


def _collect_known_game_names(ctx: "_DetectionContext", entities: list) -> set:
    """Normalised names of games/apps known to be installed in this scan.

    Sources: game entities detected by platform passes + portable/installed
    app entities + the Windows uninstall registry. Used to decide whether a
    save folder's owning game is still present.
    """
    names: set = set()
    for e in entities:
        if e.entity_type in ("game", "application", "portable_app"):
            n = _normalize_game_name(e.name)
            if n:
                names.add(n)
    for info in ctx.installed_apps.values():
        if isinstance(info, dict):
            n = _normalize_game_name(info.get("name", ""))
            if n:
                names.add(n)
    return names


# Default Windows content folders. A folder sitting directly inside one of
# these was almost always created BY an application to store its data there —
# Documents/Klei (Don't Starve saves), Documents/My Games, Pictures/<app>.
_USER_CONTENT_ROOT_NAMES = {
    "documents", "my documents", "videos", "my videos",
    "pictures", "my pictures", "music", "my music", "saved games",
}


# Classifications weak enough that "sits in an install root" outranks them.
# installer_group is here because a program directory is mostly executables:
# on extension alone Microsoft OneDrive and Ollama both read as installer
# collections, which put an installed app in Installers at recycle-able risk.
# application_data is here because a direct child of an install root IS the
# program — Ollama landed there and read as "Application Data".
_INSTALL_ROOT_OVERRIDABLE = ("unknown_folder", "mixed_folder",
                             "installer_group", "installer", "application_data")


def _enrich_program_files_apps(entities: list) -> int:
    """A top-level folder in an install root is an installed application.

    Plenty of real installs never match the uninstall registry by folder name —
    they register under a product name, ship as components, or don't register at
    all. On a real C:/ scan that left 15 GB of obvious applications sitting in
    "Unknown" with descriptor-noise names: "gstreamer · documents",
    "Fortinet · installers and logs & backups", "Razer · code & config and
    images", plus Microsoft SQL Server, Google, draw.io, OpenVPN and others.

    Covers both install roots: C:/Program Files/<app> and the per-user
    equivalent C:/Users/<u>/AppData/Local/Programs/<app>. Being a direct child
    of one is strong evidence on its own, so these become applications with
    their plain folder name. Only generic classifications are replaced —
    anything a pass identified specifically (a game, a dev environment) keeps
    its answer.
    """
    changed = 0
    for e in entities:
        if e.entity_type not in _INSTALL_ROOT_OVERRIDABLE:
            continue
        norm = e.path.replace("\\", "/").rstrip("/").lower()
        if not _is_install_root_child(norm):
            continue
        name = os.path.basename(e.path.replace("\\", "/").rstrip("/"))
        if not name:
            continue
        where = norm.split("/")[1]
        where = "Program Files" if where in _INSTALL_ROOT_NAMES else "Programs"
        e.entity_type = "application"
        e.name = name
        e.risk_reason = Reason(
            "Installed application in {where} — remove it through its own "
            "uninstaller, not by deleting the folder", where=where)
        e.summary = f"Installed application · {name}"
        changed += 1
    return changed


def _enrich_place_origin(entities: list) -> int:
    """Everything in a dump folder is filed under that folder, whatever it is.

    Reported from a full C:/ scan: "odd that we have downloads section - and
    also showing files from downloads in other section". Both were true.
    _pass_downloads claims each subfolder as a download_item (category
    Downloads), and the loose files left behind then fall through the type
    bucketer — archives to Archives, .exe/.msi to Installers, everything else to
    Unknown. Measured on the reporting machine: Downloads spread over six
    categories, Desktop over four (0.41 GB of it filed as "Unknown", on a folder
    whose contents the user can literally see).

    The underlying mix-up is one axis answering two questions. Archives /
    Installers / Images answer "what is it"; Downloads and Desktop answer "where
    is it". Location wins where a location was meant, and nothing else changes:
    entity_type still drives risk, actionability and the detail panel, so an
    installer sitting in Downloads is still an installer.
    """
    changed = 0
    for e in entities:
        norm = e.path.replace("\\", "/").rstrip("/").lower()
        _root, label = _origin_root_of(norm)
        if label:
            e.origin = label
            changed += 1
    return changed


def _enrich_drive_root_apps(entities: list) -> int:
    """A drive-root folder that names a present application is its install dir.

    Plenty of software installs outside Program Files — measured on a real
    machine: ffmpeg 11 GB, Qt 17 GB, PyCharm 1.9 GB, Webots 1 GB, two Irizi
    products 1.5 GB, all sitting directly under C:\\.

    "Contains executables" would be a weak signal (so does a download folder).
    Instead this asks the presence resolver whether an application of that name
    is actually on the machine — ffmpeg answers via PATH, PyCharm via the Start
    Menu, Irizi via the registry. No evidence, no relabel: a plain data folder
    like C:\\symbols is left alone.
    """
    from app.services.app_presence import presence, PRESENT

    changed = 0
    for e in entities:
        if e.entity_type not in ("unknown_folder", "mixed_folder"):
            continue
        norm = e.path.replace("\\", "/").rstrip("/").lower()
        parts = norm.split("/")
        if len(parts) != 2 or not parts[1]:          # <drive>/<folder> only
            continue
        name = os.path.basename(e.path.replace("\\", "/").rstrip("/"))
        state, source = presence(name, strong_only=True)
        if state != PRESENT:
            continue
        e.entity_type = "application"
        e.name = name
        e.risk_reason = Reason(
            "Installed application (found in {source}) — remove it through its "
            "own uninstaller, not by deleting the folder", source=source)
        e.summary = f"Installed application · {name}"
        changed += 1
    return changed


def _enrich_support_folders(entities: list) -> int:
    """Label dotfolder app data with what we can actually prove about its owner.

    A dotfolder in the user profile is support data for some program — one rule
    covering 34 folders / 14 GB on a real machine, including tools no curated
    list would contain (.irizi, .nexe, .node-red).

    The owner's status comes from app_presence, which reports PRESENT or
    UNKNOWN and never "absent": .vscode and .lmstudio (5.6 GB together) look
    orphaned to the uninstall registry yet are plainly installed. UNKNOWN is
    surfaced as "could not confirm", never as "safe to delete".
    """
    from app.services.app_presence import presence, describe, GENERIC

    changed = 0
    for e in entities:
        norm = e.path.replace("\\", "/").rstrip("/")
        leaf = os.path.basename(norm)
        if not leaf.startswith(".") or len(leaf) < 3:
            continue
        # Only profile-level support folders — not every dot-directory nested
        # deep inside a project (.git objects and friends stay as they are).
        if not _is_user_home_dir(os.path.dirname(norm).lower()):
            continue
        state, _source = presence(leaf)
        owner = leaf.lstrip(".")

        # A pass that already found something specific (ai_models for .ollama,
        # dev_artifacts for .vscode) knows more than "application data" — keep
        # its type and name, and only add what we can prove about the owner.
        generic_type = e.entity_type in ("unknown_folder", "mixed_folder")
        if generic_type:
            e.entity_type = "application_data"
            if state == GENERIC:
                e.name = leaf
                e.summary = f"Support folder · {leaf}"
            else:
                e.name = f"{owner} (app data)"
                e.summary = f"App data · {owner}"
        e.risk_reason = describe(leaf)
        changed += 1
    return changed


def _enrich_user_content_subfolders(entities: list) -> int:
    """Name app-owned folders in Documents/Videos/Pictures/Music honestly.

    A direct child of a user content folder is application data, not an
    unclassified pile: Documents/Klei (58 MB of Don't Starve saves) was showing
    as "Klei · documents and code & config" typed unknown_folder, which tells
    the user nothing and reads as noise.

    Only the label and type change — the folder is still grouped exactly as
    before, so sizes and containment are untouched. Whether the owning app is
    still installed is deliberately NOT decided here: that needs alias handling
    and several evidence sources to be safe (see the knowledge-base plan), and
    a wrong "orphaned" verdict would invite deleting live data.
    """
    changed = 0
    for e in entities:
        if e.entity_type not in ("unknown_folder", "mixed_folder"):
            continue
        norm = e.path.replace("\\", "/").rstrip("/")
        parent_leaf = os.path.basename(os.path.dirname(norm)).lower()
        if parent_leaf not in _USER_CONTENT_ROOT_NAMES:
            continue
        leaf = os.path.basename(norm)
        if not leaf:
            continue
        where = os.path.basename(os.path.dirname(norm))
        e.entity_type = "application_data"
        e.name = leaf                      # drop the "· docs and code" noise
        e.risk_reason = Reason(
            "Application data stored in {where} by {leaf} — keep it if you "
            "still use that program", where=where, leaf=leaf)
        e.summary = f"App data · {leaf} · stored in {where}"
        changed += 1
    return changed


def _enrich_game_saves(ctx: "_DetectionContext", entities: list):
    """Give every game_saves entity an owning game and install status.

    Turns a bare "Saves" / "Saved Games" finding into something actionable,
    e.g. "Skyrim Saves — game still installed" or
    "Game Saves — Witcher 3, Portal 2 (+2) · owning games not found in scan".
    """
    known_games = _collect_known_game_names(ctx, entities)
    enriched = 0
    for e in entities:
        if e.entity_type != "game_saves":
            continue
        norm = e.path.replace("\\", "/").lower().rstrip("/")
        leaf = os.path.basename(norm)

        # ── Per-game save engine (Ren'Py, LÖVE, …) ─────────────────────
        # The parent is the engine folder, so the leaf IS the game (build-id
        # stripped, original casing kept). Report install status and creation
        # date so the user can spot saves for games they no longer have.
        parent_leaf = os.path.basename(os.path.dirname(norm))
        if parent_leaf in _PER_GAME_SAVE_ENGINES:
            game = _clean_game_name(os.path.basename(e.path))
            installed = _game_is_installed(_normalize_game_name(game), known_games)
            created = _folder_created_date(e.path)
            e.name = game
            status = "installed" if installed else "not installed"
            reason = [f"Save data for {game}", f"game {status}"]
            if created:
                reason.append(f"created {created}")
            if not installed:
                reason.append("likely leftover if you no longer play it")
            e.risk_reason = " · ".join(reason)
            e.summary = (f"Game Saves · {game} · game {status}"
                         + (f" · created {created}" if created else ""))
            enriched += 1
            continue

        # ── Multi-game container (Saved Games / My Games) ──────────────
        if leaf in _SAVE_CONTAINER_MARKERS:
            child_games = [
                c.name for c in ctx.children_index.get(norm, [])
                if c.is_dir and c.name.lower() not in _SAVE_LEAF_MARKERS
            ]
            if child_games:
                installed = [g for g in child_games
                             if _game_is_installed(_normalize_game_name(g), known_games)]
                shown = ", ".join(child_games[:3])
                extra = len(child_games) - 3
                if extra > 0:
                    shown += f" (+{extra})"
                e.name = f"Game Saves — {shown}"
                if installed and len(installed) == len(child_games):
                    status = "all owning games still installed"
                elif installed:
                    status = (f"{len(installed)} of {len(child_games)} owning games "
                              f"still installed")
                else:
                    status = "none of the owning games were found in this scan"
                e.risk_reason = f"Save data for {len(child_games)} game(s) · {status}"
                e.summary = (f"Game Saves · {len(child_games)} games · "
                             f"{e.file_count:,} files · {e.size}")
                enriched += 1
                continue

        # ── Single-game save folder ────────────────────────────────────
        game_seg = _extract_owning_game(norm)
        if game_seg:
            display_game = _pretty_game(game_seg)
            installed = _game_is_installed(_normalize_game_name(game_seg), known_games)
            e.name = f"{display_game} Saves"
            if installed:
                e.risk_reason = Reason("Save data for {game} — game still installed",
                                       game=display_game)
            else:
                e.risk_reason = Reason(
                    "Save data for {game} — owning game not found in this "
                    "scan (may be uninstalled)", game=display_game)
            e.summary = (f"Game Saves · {display_game} · "
                         f"{e.file_count:,} files · {e.size}")
            enriched += 1
        elif not e.risk_reason or e.risk_reason.lower().startswith("entity type:"):
            e.risk_reason = Reason(
                "Game/app save data — owning game could not be determined "
                "from the path")
            enriched += 1

    if enriched:
        ctx.log(f"[smart] resolved owning-game context for {enriched} save "
                f"entit{'y' if enriched == 1 else 'ies'}")


# Subtrees of C:/Windows that must never be offered for cleanup, whatever a
# classification pass decided. These hold OS application packages and the
# servicing/component store: hand-deleting them breaks Windows features or
# Windows Update. Deliberately narrow — Windows/Temp, Windows/Logs and the
# Windows Update download cache stay cleanable, because they genuinely are.
_NEVER_CLEAN_WINDOWS_SUBTREES = {
    "systemapps",   # OS app packages (Cortana, CloudExperienceHost, …) + their assets
    "winsxs",       # component store; Microsoft: never delete by hand
    "servicing",    # servicing stack / packages — breaks Windows Update
    "assembly",     # GAC
}


def _enforce_system_protection(entities: list, log_fn=None) -> int:
    """Force Protected on entities inside never-clean OS subtrees.

    Risk is assigned by whichever pass claims an entity first, so a cache or
    image pass could label OS app assets Safe/Review before protection was ever
    considered — e.g. "Cache – Cortana.Ui" (Safe) and six "Images – …"
    collections under Windows/SystemApps. Applying this once at the end is a
    single choke point that covers every pass, including future ones.
    """
    changed = 0
    for e in entities:
        parts = e.path.replace("\\", "/").lower().rstrip("/").split("/")
        # parts[0]=drive, parts[1]=windows, parts[2]=subtree
        if len(parts) >= 3 and parts[1] == "windows" \
                and parts[2] in _NEVER_CLEAN_WINDOWS_SUBTREES:
            if e.risk != RISK_PROTECTED:
                e.risk = RISK_PROTECTED
                e.risk_reason = Reason(
                    "Windows {subtree} — part of the operating system; "
                    "removing it can break Windows features or updates",
                    subtree=parts[2])
                changed += 1
    if changed and log_fn:
        log_fn(f"[smart] protected {changed} entities inside Windows system subtrees")
    return changed


def _disambiguate_names(entities: list) -> int:
    """Append a path hint to entities that share a display name.

    Two "Qt" installs, three "python" trees — a bare repeated name is
    unscannable. For each colliding name, append the deepest path segment that
    differs from the name (usually a version or install folder), e.g.
    "Qt (6.5.0)" / "Qt (5.15.2)". Best-effort and display-only: it never touches
    size, path, or the AI cache key.
    """
    from collections import defaultdict
    groups: dict[str, list] = defaultdict(list)
    for e in entities:
        # Keyed case-insensitively: Windows paths are, and two loose buckets
        # came out as "Loose archives in Downloads" and "Loose archives in
        # downloads" for two unrelated folders. Different rows, and nothing on
        # either said so — they read as the same row printed twice.
        groups[e.name.lower()].append(e)

    renamed = 0
    for group in groups.values():
        if len(group) < 2:
            continue
        name_words = {w for w in re.split(r"[^a-z0-9]+", group[0].name.lower())
                      if w}
        seg_lists = [[p for p in e.path.replace("\\", "/").split("/") if p]
                     for e in group]
        for e, parts in zip(group, seg_lists):
            hint = _distinguishing_segment(parts, seg_lists, name_words)
            hint = _shorten_disambiguation_hint(_trim_hint(hint, name_words))
            if hint:
                e.name = _with_hint(e.name, hint)
                renamed += 1
    return renamed


def _trim_hint(hint: str, name_words: set[str]) -> str:
    """Drop from *hint* the words the name already says.

    Two installers in Downloads produced "Installer (Stream_Brave1)
    (Stream_Brave1_1.0.8.exe)" — the hint restated the product and buried the
    only part that told them apart. What is left here is "1.0.8.exe".
    """
    if not hint:
        return ""
    kept = [w for w in re.split(r"[\s_]+", hint)
            if w and w.lower() not in name_words]
    trimmed = " ".join(kept).strip(" -_.")
    return trimmed or hint


def _with_hint(name: str, hint: str) -> str:
    """"Qt" + "6.5.0" -> "Qt (6.5.0)"; a name that already has a parenthetical
    takes the hint inside it rather than growing a second pair."""
    if name.endswith(")") and "(" in name:
        head, _, tail = name.rpartition(" (")
        if head.strip():
            return f"{head} ({tail[:-1]} · {hint})"
    return f"{name} ({hint})"


def _distinguishing_segment(parts: list[str], siblings: list[list[str]],
                            name_words: set[str]) -> str:
    """The path segment that tells *this* entity apart from the others.

    The old rule took the deepest segment that differed from the display
    name, which on a real scan produced nine rows all called "Loose AI model
    files in downloads (downloads)": the hint repeated a word already in the
    name and was identical for every one of them, so the thing it existed to
    disambiguate stayed ambiguous.

    Walking from the leaf, a segment qualifies when it is not already said in
    the name and no sibling carries the same segment at the same depth — that
    is, when it is the reason these two rows are different rows.
    """
    fallback = ""
    for i in range(len(parts)):
        seg = parts[len(parts) - 1 - i]
        low = seg.lower()
        if low in name_words or low.endswith(":"):
            continue
        others = [o for o in siblings if o is not parts]
        at_depth = [o[len(o) - 1 - i].lower() for o in others if len(o) > i]
        if low not in at_depth:
            return seg
        if not fallback and any(v != low for v in at_depth):
            fallback = seg
    return fallback or (parts[0] if parts else "")


# Verbose container segments compressed to a short tag when used as a
# disambiguation hint — "Microsoft (Program Files (x86))" reads as clutter, so
# the bitness (the part that actually distinguishes the two installs) is what we
# keep: "Microsoft (x86)" / "Microsoft (64-bit)".
_HINT_COMPRESSION = {
    "program files (x86)": "x86",
    "program files": "64-bit",
    "programdata": "shared",
}


def _shorten_disambiguation_hint(hint: str) -> str:
    return _HINT_COMPRESSION.get(hint.strip().lower(), hint)


def _enforce_disjoint_sizes(ctx: "_DetectionContext", entities: list, log_fn) -> int:
    """Charge every scanned byte to exactly one entity.

    A folder-rooted entity measures its whole subtree. When a sub-entity inside
    it survives post-processing — ``node_modules``, ``venv``, ``dev_artifacts``
    and ``shader_cache`` are deliberately *retained* inside apps/games for their
    cleanup value — those bytes would otherwise be counted twice: once in the
    child and once in the parent. Here each surviving sub-entity is subtracted
    from its nearest surviving ancestor.

    Nesting works out because a child's subtree total already includes anything
    nested below it, and each child is charged to exactly one ancestor. For
    A ⊃ B ⊃ C this yields A−B, B−C, C — which sums back to A.

    Only subtree-backed entities take part. Loose-file buckets, installer and
    archive groups stand for a filtered set of files rather than a folder, so
    subtracting a nested folder from them would be meaningless.
    """
    def _norm(p: str) -> str:
        return p.replace("\\", "/").lower()

    backed = [(e, _norm(e.path)) for e in entities
              if _norm(e.path) in ctx.subtree_entity_paths]
    if len(backed) < 2:
        return 0

    adjusted = 0
    for child, c_norm in backed:
        owner, owner_depth = None, -1
        for parent, p_norm in backed:
            if p_norm != c_norm and c_norm.startswith(p_norm + "/"):
                depth = p_norm.count("/")
                if depth > owner_depth:   # nearest (deepest) surviving ancestor
                    owner, owner_depth = parent, depth
        if owner is None:
            continue
        c_size, c_files, c_folders, _, _ = ctx.subtree(c_norm)
        owner.size_bytes = max(0, owner.size_bytes - c_size)
        owner.file_count = max(0, owner.file_count - c_files)
        # +1 for the child's own directory, which the parent also counted.
        owner.folder_count = max(0, owner.folder_count - c_folders - 1)
        adjusted += 1

    if adjusted:
        # size / summary are derived from size_bytes at construction time.
        for e in entities:
            e.size = _format_size(e.size_bytes)
            type_label = ENTITY_TYPES.get(e.entity_type, e.entity_type)
            e.summary = f"{type_label} · {e.file_count:,} files · {e.size}"
        log_fn(f"[smart] disjoint sizes: {adjusted} nested entit"
               f"{'y' if adjusted == 1 else 'ies'} charged to one owner")
    return adjusted


# A folder of projects is not a project. Measured on a real E:/ scan:
# "E:/My Projects" came back as one dev_project of 261.7 GB while holding four
# separate projects and five colmap databases, because _WEAK_NAME_FOLDER_TYPES
# matches the word "project" in a name — a folder named for the plural got
# classified as the singular. "_src" had the same shape: a 2.4 GB mixed_folder
# holding 30 GB across four checkouts.
_WORKSPACE_CANDIDATE_TYPES = frozenset({
    "dev_project", "mixed_folder", "unknown_folder", "development_environment",
})
_PROJECT_LIKE_TYPES = frozenset({"dev_project", "dev_workspace"})

# Two is a collection. One project inside a folder is a project in a folder.
_WORKSPACE_MIN_PROJECTS = 2


def _retype_workspaces(ctx: "_DetectionContext", entities: list, log_fn):
    """Relabel containers of projects, so the row matches what is inside it.

    The test is the one a person would apply: does this folder hold several
    separate projects, and is it not itself one? The second half matters —
    "irizi-odm-dev" vendors two source checkouts and would otherwise be
    called a workspace, when it is a project that happens to contain them.
    Its own project markers are what say so.

    Deliberately the same "lives directly inside" relation the inspector's
    ITEMS list uses, so the label and the list can never disagree.
    """
    def norm(e):
        return e.path.replace("\\", "/").lower().rstrip("/")

    project_like = [(norm(e), e) for e in entities
                    if e.entity_type in _PROJECT_LIKE_TYPES]
    if not project_like:
        return

    retyped = 0
    for entity in entities:
        if entity.entity_type not in _WORKSPACE_CANDIDATE_TYPES:
            continue
        root = norm(entity)
        if not root or root == ctx.root_norm or _is_drive_root(root):
            continue
        # Pass 8 buckets carry the enclosing folder as their path; they own no
        # subtree and can never be a workspace.
        if getattr(entity, "removable_file_paths", None):
            continue

        inside = [(p, e) for p, e in project_like
                  if p != root and p.startswith(root + "/")]
        # Only the outermost ones: a project vendored inside another project
        # is that project's business, not this folder's.
        direct = [e for p, e in inside
                  if not any(q != p and p.startswith(q + "/")
                             for q, _ in inside)]
        if len(direct) < _WORKSPACE_MIN_PROJECTS:
            continue

        # Does it claim to be a project in its own right?
        own_files = {c.name.lower() for c in ctx.gather_direct(root)
                     if not c.is_dir}
        if own_files & _PROJECT_MARKER_FILES:
            continue
        if {os.path.splitext(n)[1] for n in own_files} & _PROJECT_MARKER_EXTS:
            continue

        entity.entity_type = "dev_workspace"
        # No count in the sentence: the ITEMS list in the inspector already
        # shows how many, and a number baked into a reason string is a number
        # that cannot be translated.
        entity.reason = Reason("Holds several separate projects rather than "
                               "being one")
        # category and actionability are properties of entity_type, so they
        # follow on their own. risk is a stored field and does not.
        entity.risk = _ENTITY_RISK.get("dev_workspace", "Review")
        retyped += 1

    if retyped:
        log_fn(f"[smart] relabelled {retyped} folders as development "
               f"workspaces rather than projects")


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
        elif (e.entity_type == "mixed_folder"
              and np in ctx.subtree_entity_paths):
            # A folder we could describe but not name is the same thing as an
            # unknown one, and hides its noisy children the same way. Only a
            # *folder-backed* one: pass 8's "Misc files in X" buckets are
            # mixed_folder too, and they carry the enclosing directory as
            # their path — as an absorber, five stray files in E:/Forge would
            # swallow every entity on the drive below it.
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

    # Resolve owning game + install status for save entities. Runs after
    # absorption so every game/app entity that could be an "owner" already
    # exists in the list.
    _enrich_game_saves(ctx, entities)

    # Folders sitting directly in Documents/Videos/Pictures/Music are app data,
    # not unclassified piles — label them as such.
    _enrich_user_content_subfolders(entities)

    # Profile dotfolders are app support data; say what we can prove about the
    # owning program, and nothing we cannot.
    _enrich_support_folders(entities)

    # A top-level Program Files folder is an installed application, even when
    # the registry does not name it.
    _enrich_program_files_apps(entities)

    # Software installed outside Program Files (C:\ffmpeg, C:\PyCharm …) is
    # still an application when the presence resolver can confirm it.
    _enrich_drive_root_apps(entities)

    # Curated rules for well-known Windows locations (thumbnail cache, package
    # caches, Windows-installed vendor folders). Annotates generic results and
    # may raise protection; never talks a specific classification down.
    from app.services.known_paths import apply_known_path_rules
    apply_known_path_rules(entities)

    # Downloads and Desktop are places, not content types — file their contents
    # under them instead of scattering each folder across the whole chip bar.
    _enrich_place_origin(entities)

    # A folder that holds several projects is a workspace, not a project.
    # After absorption, so the children it is counted against are the ones
    # that actually survive to be listed.
    _retype_workspaces(ctx, entities, log_fn)

    # Charge every byte to exactly one entity. Must run after absorption, since
    # only the entities that actually survive can hold a share of the bytes.
    _enforce_disjoint_sizes(ctx, entities, log_fn)

    # Rule 2 (system protection): OS app packages and the servicing/component
    # store are never cleanable, whatever an earlier pass decided. Runs after
    # absorption so it applies to the entities that actually survive.
    _enforce_system_protection(entities, log_fn)

    # Disambiguate identically-named entities (e.g. two "Qt" installs) so the
    # list is scannable. Runs last so absorbed/renamed entities are settled.
    _disambiguate_names(entities)

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
                        e.risk_reason = Reason("cloud-synced ({provider})", provider=provider)
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



def detector_reason_templates() -> set:
    """Every Reason(...) template this module can produce.

    Reasons reach tr() through a stored key, so no static scan over tr("...")
    calls finds them. Read out of this module's own source so the list cannot
    drift from the code — adding a Reason without a translation fails the
    coverage test rather than shipping English into a translated build.
    """
    import ast as _ast
    import pathlib as _pathlib

    source = _pathlib.Path(__file__).read_text(encoding="utf-8")
    keys = set()
    for node in _ast.walk(_ast.parse(source)):
        if not (isinstance(node, _ast.Call)
                and getattr(node.func, "id", "") == "Reason" and node.args):
            continue
        first = node.args[0]
        if isinstance(first, _ast.Constant) and isinstance(first.value, str):
            keys.add(first.value)
        elif isinstance(first, _ast.JoinedStr):
            keys.add("".join(v.value for v in first.values
                             if isinstance(v, _ast.Constant)))
    return keys


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
    # Treat pure structure (C:/Users, AppData and its Local/LocalLow/Roaming)
    # as nodes only: claimed so no pass turns them into one unactionable blob,
    # but never emitted, so their per-application children are classified on
    # their own. Anything loose directly inside them still lands in a pass-8
    # bucket, so nothing goes missing.
    for f in ctx.all_dirs:
        norm_path = f.path.replace("\\", "/").lower().rstrip("/")
        if _is_user_container_dir(norm_path) or _is_appdata_container_dir(norm_path):
            ctx.claimed_paths.add(norm_path)

    # Podbye's own folders first — nothing else may classify them.
    _pass_self(ctx)
    _pass0_update_caches(ctx)
    _pass_appdata_packages(ctx)
    _pass_game_save_engines(ctx)
    # Downloads first: each downloaded folder is claimed whole before any
    # generic pass can shatter it into fragments.
    _pass_downloads(ctx)
    # Explode diverse dump roots BEFORE pass 1, otherwise generic names like
    # "documents"/"downloads" in _DIR_ENTITY_MAP get claimed as one blob first.
    _pass_explode_user_roots(ctx)
    # Browser caches before pass 1: "Cache"/"GPUCache" are also generic names in
    # _DIR_ENTITY_MAP, so whichever pass reached them first decided the label.
    # That made presentation depend on how deep a profile happened to sit —
    # most folders got "Chrome cache · Default / Cache" while ShaderCache and
    # GPUCache fell through to a bare "Known directory: ShaderCache". Claiming
    # them here makes the naming uniform, and the profile pass still runs later
    # so it keeps only the irreplaceable data.
    # Before both: an Electron app's CachedData/<hash>/chrome is not a browser.
    _pass_electron_code_cache(ctx)
    _pass_browser_caches(ctx)
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
    ctx,
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
    uninstall_string: str = "",
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

    # Size and counts come from the exact subtree aggregate, never from
    # `children` — that is a capped sample (see _DetectionContext.sample) and
    # summing it silently under-reported every folder above the cap.
    norm_path = path.replace("\\", "/").lower()
    subtree_backed = ctx is not None and norm_path in ctx.subtree_stats
    if subtree_backed:
        total_size, file_count, folder_count, mtime, atime = ctx.subtree(norm_path)
        # This entity's size stands for a whole subtree, so it participates in
        # the disjointness correction (see _enforce_disjoint_sizes).
        ctx.subtree_entity_paths.add(norm_path)
    else:
        total_size = sum(c.size_bytes for c in children)
        file_count = len(files)
        folder_count = len(dirs)
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

    # For installer/archive folders, cleanup should target only the matching
    # files (the .exe/.zip), never the whole containing folder (e.g. Downloads).
    removable: list = []
    if entity_type in ("installer", "installer_group", "archive_group"):
        # Walk the full subtree only for the types that need it — cleanup must
        # target every matching file, not just the ones in the capped sample.
        pool = ctx.subtree_files(norm_path) if subtree_backed else files
        if entity_type == "archive_group":
            removable = [c.path for c in pool
                         if (c.extension or "").lower() in _ARCHIVE_EXTS]
        else:
            # Only genuine installers, so recycling an installer group never
            # sweeps up a program executable that happens to sit alongside.
            removable = [c.path for c in pool
                         if _looks_like_installer_file(c.name, c.extension)]

    return SmartEntity(
        path=path,
        name=name,
        entity_type=entity_type,
        size_bytes=total_size,
        file_count=file_count,
        folder_count=folder_count,
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
        uninstall_string=uninstall_string,
        removable_file_paths=removable,
    )


# A folder's own loose files only describe the folder when they account for a
# real share of it. Below this they are incidental — a README beside twenty
# subfolders — and the subfolders are what the folder actually is.
_REPRESENTATIVE_DIRECT_SHARE = 0.2


def _direct_files_describe_folder(direct_files: list[Finding],
                                  descendants: list[Finding]) -> bool:
    """True when a folder's own loose files can speak for the whole folder.

    _classify_by_content reads a folder's DIRECT children, but the entity it
    labels reports RECURSIVE totals. When the two disagree the label is drawn
    from a sample that is nothing like the thing being measured.

    Reported against an all-drives scan: the only loose file at D:\\ is
    RESULTS.md, so the ratio of document extensions among direct files was
    1.0, the drive root was labelled "Documents Folder", and the row then
    displayed the drive's recursive totals — 392,273 files, 645 GB of mostly
    aerial imagery. Same shape for coordinate_recovery_outputs: 187,555 files,
    0.0% documents overall, labelled from a handful of loose files at its top.
    """
    if not direct_files:
        return False
    descendant_files = [c for c in descendants if not c.is_dir]
    total = sum(f.size_bytes for f in descendant_files)
    if total <= 0:
        return True                      # nothing to weigh against
    direct_size = sum(f.size_bytes for f in direct_files)
    return (direct_size / total) >= _REPRESENTATIVE_DIRECT_SHARE


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
    # Counted separately from any extension: a model store full of
    # extensionless hash blobs has no extension to be recognised by.
    model_count = 0
    model_size = 0

    for f in files:
        ext = f.extension.lower() if f.extension else ""
        ext_counts[ext] += 1
        ext_sizes[ext] += f.size_bytes
        if _is_model_file(f):
            model_count += 1
            model_size += f.size_bytes

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
    
    # AI/ML Models - high priority (large files, specific extensions).
    # Judged by _is_model_file, never by the bare extension: a folder of 2 MB
    # ".bin" blobs is a cache, and calling it a model store made it a
    # high-priority absorber that swallowed the cache entity inside it.
    if model_count / total > 0.2 or model_size / total_sz > 0.4:
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
    
    # Installers — counted per file, not per extension: an .exe only counts
    # when its name shows installer intent (_looks_like_installer_file), so a
    # folder of program executables is not read as a pile of installers.
    installer_like = sum(1 for f in files
                         if _looks_like_installer_file(f.name, f.extension))
    if installer_like / total > 0.4:
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


# A word in a folder's name, used only when the files inside say nothing at
# all. Weak evidence by design: "output" is not a backup, and "Docs" beside
# 50 GB of photographs is not a document folder.
_KEYWORD_FOLDER_TYPES = (
    (("cache", "caches", "tmp", "temp"), "cache_folder"),
    (("log", "logs", "diag", "trace", "dump", "crash", "error"), "log_folder"),
    (("backup", "backups", "bak", "old", "archive"), "backup_group"),
    (("install", "setup", "update", "patch", "deploy"), "installer"),
    (("export", "exports", "output", "report"), "backup_group"),
    (("photo", "image", "picture", "screenshot"), "photo_collection"),
    (("video", "movie", "film", "clip"), "video_collection"),
    (("music", "audio", "sound", "song"), "audio_collection"),
    (("doc", "document", "manual", "guide", "readme"), "document_folder"),
)


def _keyword_folder_type(lower_name: str) -> str:
    """Entity type suggested by a word in the folder name, or ""."""
    for keywords, etype in _KEYWORD_FOLDER_TYPES:
        if any(k in lower_name for k in keywords):
            return etype
    return ""


# Content groups for the byte-weighted tie-break below.
_CONTENT_GROUPS = (
    ("photo_collection", _IMAGE_EXTS),
    ("video_collection", _VIDEO_EXTS),
    ("audio_collection", _AUDIO_EXTS),
    ("document_folder", _DOC_EXTS),
    ("archive_group", _ARCHIVE_EXTS),
    ("dataset", _PHOTOGRAMMETRY_EXTS),
    ("database", _DATABASE_EXTS),
    ("log_folder", _LOG_EXTS | _BACKUP_EXTS),
)


def _plurality_content_type(files: list, min_share: float = 0.35,
                            lead: float = 1.5) -> str:
    """The kind most of a diverse folder's files belong to, or "".

    _classify_by_content asks each kind in turn whether it clears a fixed
    threshold, so a folder where several kinds are present and none reaches
    50% gets no answer at all — and the caller then guessed from the folder's
    name. coordinate_recovery_outputs is that folder: aerial imagery,
    orthophotos, point clouds and their sidecars, no single extension set over
    the line, and the name-based guess that followed said "backup".

    So when nothing dominates outright, the commonest kind answers — but only
    when it holds a real share of the folder and leads the runner-up clearly.
    A genuinely mixed folder still gets no answer, which is what mixed should
    mean.

    Counts, not bytes. The input is a capped sample, and one 20 GB archive in
    it outweighs ten thousand source files: weighing by bytes called
    E:/My Projects — 261 GB of repositories — an archive collection. A count
    cannot be swung by a single file.
    """
    files = [f for f in files if not f.is_dir]
    if not files:
        return ""
    total = len(files)
    scores = []
    for etype, exts in _CONTENT_GROUPS:
        share = sum(1 for f in files
                    if (f.extension or "").lower() in exts) / total
        if share > 0:
            scores.append((share, etype))
    if not scores:
        return ""
    scores.sort(reverse=True)
    best_share, best_type = scores[0]
    runner_up = scores[1][0] if len(scores) > 1 else 0.0
    if best_share < min_share:
        return ""
    if runner_up and best_share < runner_up * lead:
        return ""
    return best_type


def _looks_like_source_tree(files: list) -> bool:
    """True for a folder that is mostly source code and configuration.

    Deliberately NOT part of _classify_by_content: that runs ahead of every
    name-based rule, and "assets/bundle.js" is build output whatever its
    extension census says. This is asked only where the alternative is
    "Unknown" — which on a real scan was 52 GB of conda environments, CMake
    trees, vcpkg builds and SDK checkouts, every row already describing itself
    as "mostly code & config" while its category said unclassified.

    Config alone is not a project: half of AppData is .json and .xml. Real
    source has to be present for the folder to be called one.
    """
    files = [f for f in files if not f.is_dir]
    if not files:
        return False
    total = len(files)
    exts = [(f.extension or "").lower() for f in files]
    code = sum(1 for e in exts if e in _CODE_EXTS)
    source = sum(1 for e in exts if e in _SOURCE_CODE_EXTS)
    return code / total > 0.5 and source / total >= 0.1
