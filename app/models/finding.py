"""Data model for a single scan finding."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime


# Largest unit first. Decimals are per-unit: the big units carry a decimal
# because one whole GB of difference is invisible at TB scale, while a decimal
# on KB/MB is noise. TB and PB are not hypothetical here — the all-time
# "analyzed" counter on Home is cumulative across every scan ever run, and it
# read "13542.8 GB" on a machine that had been scanning drives for a month.
_SIZE_UNITS = (
    ("PB", 1024 ** 5, 1),
    ("TB", 1024 ** 4, 1),
    ("GB", 1024 ** 3, 1),
    ("MB", 1024 ** 2, 0),
    ("KB", 1024,      0),
    ("B",  1,         0),
)


def _format_size(size_bytes: int) -> str:
    """Byte count in the largest unit that keeps the number readable."""
    for i, (unit, scale, places) in enumerate(_SIZE_UNITS):
        if size_bytes < scale:
            continue
        value = size_bytes / scale
        # The unit is chosen by threshold but the value is rounded for display,
        # and the two can disagree at a boundary: 1073741823 B is just under
        # 1 GB, so it picks MB, where .0f rounds 1023.999… up to a nonsensical
        # "1024 MB". Promote to the next unit when rounding carries.
        if i > 0 and round(value, places) >= 1024:
            unit, scale, places = _SIZE_UNITS[i - 1]
            value = size_bytes / scale
        return f"{value:.{places}f} {unit}"
    return f"{size_bytes} B"


def split_size(size_bytes: int) -> tuple[str, str]:
    """``_format_size`` split into (number, unit) for the big two-label readouts.

    Screens that style the number and the unit differently used to pick the
    unit with their own if-ladder, which is how they stayed capped at GB after
    the shared formatter learned about TB. Splitting the formatted string keeps
    them honest by construction.
    """
    number, _, unit = _format_size(size_bytes).partition(" ")
    return number, unit


def _format_age(mtime: float) -> str:
    days = int((time.time() - mtime) / 86400)
    if days <= 0:
        return "0d"
    if days < 30:
        return f"{days}d"
    months = days // 30
    if months < 12:
        return f"{months}m"
    years = months // 12
    rem = months % 12
    return f"{years}y {rem}m" if rem else f"{years}y"


# ═══════════════════════════════════════════════════════════════════
#  CATEGORY RULE CONSTANTS
#  All collections are module-level frozensets — zero per-call alloc.
# ═══════════════════════════════════════════════════════════════════

_CACHE_KEYWORDS = frozenset({"temp", "cache", "tmp", "thumbcache", "__pycache__"})

_BROWSER_CACHE_KEYWORDS = frozenset({
    "chrome", "firefox", "edge", "opera", "brave", "vivaldi",
})

_DEV_ARTIFACT_NAMES = frozenset({
    "node_modules", ".venv", "venv", "__pycache__", "dist", "build",
    ".gradle", "target", ".tox", ".mypy_cache", ".pytest_cache",
    ".next", ".nuxt", "bower_components", ".parcel-cache", ".turbo",
    ".svelte-kit", ".output", ".cache",
})

# Per-directory semantic labels for known dev-artifact dirs.
# (semantic_label, owner_confidence)
_DEV_ARTIFACT_LABELS = {
    "node_modules":    ("Dependency Store",  "exact"),
    ".venv":           ("Python Environment","exact"),
    "venv":            ("Python Environment","exact"),
    "__pycache__":     ("Build Cache",       "exact"),
    "dist":            ("Build Output",      "probable"),
    "build":           ("Build Output",      "probable"),
    ".gradle":         ("Build Cache",       "exact"),
    "target":          ("Build Output",      "exact"),
    ".next":           ("Build Cache",       "exact"),
    ".nuxt":           ("Build Cache",       "exact"),
    "bower_components":("Dependency Store",  "exact"),
    ".parcel-cache":   ("Build Cache",       "exact"),
    ".turbo":          ("Build Cache",       "exact"),
    ".svelte-kit":     ("Build Cache",       "exact"),
    ".output":         ("Build Output",      "exact"),
    ".cache":          ("Build Cache",       "exact"),
    ".tox":            ("Build Cache",       "exact"),
    ".mypy_cache":     ("Build Cache",       "exact"),
    ".pytest_cache":   ("Build Cache",       "exact"),
}

_ARCHIVE_EXTS = frozenset({
    ".zip", ".rar", ".7z", ".tar", ".gz", ".iso", ".bz2", ".xz", ".lz4", ".zst",
})

# Split media sets so All-files mode produces the SAME Images/Videos/Audio
# categories as Adaptive mode (instead of one coarse "Media"). _MEDIA_EXTS is
# kept as their union for any caller that wants the umbrella set.
_IMAGE_EXTS = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif", ".raw", ".svg",
    ".webp", ".heic", ".heif", ".cr2", ".nef", ".arw", ".dng",
})
_VIDEO_EXTS = frozenset({
    ".mp4", ".mkv", ".mov", ".avi", ".wmv", ".flv", ".webm", ".m4v", ".ts", ".vob",
})
_AUDIO_EXTS = frozenset({
    ".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a", ".opus", ".aiff",
})
_MEDIA_EXTS = _IMAGE_EXTS | _VIDEO_EXTS | _AUDIO_EXTS

_LOG_EXTS = frozenset({".log", ".logs"})

_APP_EXTS = frozenset({".exe", ".msi", ".appx", ".msix"})

_DOC_EXTS = frozenset({
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".odt", ".ods", ".odp", ".rtf", ".epub", ".md", ".markdown",
    ".pages", ".numbers", ".key", ".tex", ".csv", ".txt",
})

_DB_EXTS = frozenset({".db", ".sqlite", ".sqlite3", ".accdb", ".mdb", ".dbf", ".sav"})

_AI_ML_EXTS = frozenset({".onnx", ".pt", ".pth", ".gguf", ".safetensors", ".ggml"})

_AI_ML_LARGE_EXTS = frozenset({".pkl", ".h5", ".pb", ".tflite", ".bin"})
_AI_ML_PATH_KEYWORDS = frozenset({
    "model", "models", "weights", "checkpoints", "llm", "ollama",
    "lora", "huggingface", "diffusers",
})

# Weights below this are configs, manifests and tokenizer files rather than a
# model — matching the threshold the extension-based rule already uses.
_MODEL_BLOB_MIN_BYTES = 50 * 1024 * 1024


def is_model_blob(name: str, ext: str, parts: frozenset, size_bytes: int) -> bool:
    """True for a large model file that carries no extension to be judged by.

    Both AI/ML rules below key on the extension, which misses the way the two
    most common local-LLM runners actually store weights: Ollama writes every
    model to ~/.ollama/models/blobs/sha256-<hash>, and HuggingFace writes
    ~/.cache/huggingface/hub/models--*/blobs/<hash>. Both are extensionless,
    so a 7 GB model landed in "Unknown" — on the runners Podbye's own README
    names. The path is the evidence here, which is why the keyword set above
    already lists "ollama" and "huggingface"; it was simply unreachable for a
    file with no extension.
    """
    if ext or size_bytes < _MODEL_BLOB_MIN_BYTES:
        return False
    return bool(parts & _AI_ML_PATH_KEYWORDS)

_SYSTEM_EXTS = frozenset({
    ".dll", ".sys", ".ocx", ".drv",
    ".inf", ".cat",
    ".ttf", ".otf", ".woff", ".woff2", ".fon", ".fnt",
})

_SOURCE_EXTS = frozenset({
    ".py", ".pyw",
    ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
    ".cs", ".java", ".kt", ".swift",
    ".cpp", ".cxx", ".cc", ".c", ".h", ".hpp",
    ".rs", ".go",
    ".rb", ".php", ".pl", ".lua",
    ".r", ".m", ".scala", ".dart",
    ".ex", ".exs", ".clj", ".hs", ".ml", ".fs",
    ".sql",
    ".sh", ".bash", ".zsh", ".fish",
    ".ps1", ".psm1", ".psd1",
    ".vbs", ".bat", ".cmd",
})


# ═══════════════════════════════════════════════════════════════════
#  PATH OWNERSHIP DETECTION
#
#  Returns (category, reason, semantic_label, owner_confidence).
#  Checked after cache/log rules but before extension rules so that
#  application-owned files override their generic extension category.
#
#  owner_confidence values:
#    "exact"     — strong structural evidence (known marker, vendor path)
#    "probable"  — good semantic match but not guaranteed
#    "heuristic" — weak pattern-based inference
# ═══════════════════════════════════════════════════════════════════

_GPU_CACHE_SEGS = frozenset({
    "dxcache", "dx_cache", "nvcache", "nvdxgistorage",
    "shadercache", "shader_cache", "glcache", "d3dcache",
    "nvshaderperf", "nvoptix", "nvdisplay.cache", "computecache",
    "gputexturecache", "llvm_gpuopen_cache",
})

_VM_SEGS = frozenset({
    "virtualbox vms",   # VirtualBox
    "vmware",           # VMware Workstation / Player
    "memuhyperv vms",   # MEmu Hyper-V
    "bluestacks_hdd",   # BlueStacks disk images
    "qemu",             # QEMU
    "kvm",
    "hyper-v",
    "wsl",              # WSL distro root
    "lxss",             # WSL legacy layer
})

_EMULATOR_SEGS = frozenset({
    "genymotion", "ldplayer",
    "noxplayer", "nox",
    "memu",             # MEmu — checked after more-specific "memuhyperv vms"
    "bluestacks",
    "microvirt",
})

_CONDA_ROOT_SEGS = frozenset({
    "miniconda3", "anaconda3", "miniforge3", "mambaforge",
    "miniconda", "anaconda", "miniforge", "mambaforge3",
})

_AI_FRAMEWORK_SEGS = frozenset({
    "huggingface", "transformers", "comfyui",
    "stable-diffusion", "stable_diffusion",
    "loras", "controlnet", "embeddings",
    "invokeai", "automatic1111",
})

_DOTNET_SEGS = frozenset({"dotnet", ".dotnet"})
_DOTNET_COMPONENT_SEGS = frozenset({
    "microsoft.netcore.app", "microsoft.aspnetcore.app",
    "microsoft.windowsdesktop.app",
})

_JAVA_SEGS = frozenset({
    "jdk", "jre", "openjdk", "adoptopenjdk",
    "temurin", "graalvm", "zulu", "liberica",
})

_CHOCO_SEGS = frozenset({"chocolatey", "choco"})

_DOCKER_SEGS = frozenset({"docker", "docker desktop", "wsl-data", "docker-desktop-data"})

# (manager_seg, context_seg, category, reason, semantic_label, confidence)
_PKG_CACHE_PAIRS = [
    ("pip",      "cache",      "Cache & Temp",  "pip package cache",          "Package Cache",    "heuristic"),
    ("pip",      "http-v2",    "Cache & Temp",  "pip HTTP cache",             "Package Cache",    "heuristic"),
    ("npm",      "cache",      "Cache & Temp",  "npm package cache",          "Package Cache",    "heuristic"),
    ("yarn",     "cache",      "Cache & Temp",  "Yarn package cache",         "Package Cache",    "heuristic"),
    ("pnpm",     "cache",      "Cache & Temp",  "pnpm package cache",         "Package Cache",    "heuristic"),
    ("nuget",    "cache",      "Dev Artifacts", "NuGet package store",        "Package Registry", "probable"),
    ("nuget",    "packages",   "Dev Artifacts", "NuGet package store",        "Package Registry", "probable"),
    ("cargo",    "registry",   "Dev Artifacts", "Cargo package registry",     "Package Registry", "probable"),
    ("cargo",    "git",        "Dev Artifacts", "Cargo git sources",          "Package Registry", "probable"),
    ("gradle",   "caches",     "Dev Artifacts", "Gradle build cache",         "Build Cache",      "probable"),
    ("maven",    "repository", "Dev Artifacts", "Maven local repository",     "Package Registry", "probable"),
    ("ivy2",     "cache",      "Dev Artifacts", "Ivy package cache",          "Package Cache",    "probable"),
    ("coursier", "cache",      "Dev Artifacts", "Coursier artifact cache",    "Package Cache",    "probable"),
]

_UPDATER_SEGS = frozenset({
    "updateframework", "ota-artifacts", "ota_artifacts",
    "temp_update", "squirrel.me",
})

# ── Application-context helpers ───────────────────────────────────────────────
# Segments that indicate the path is inside an installed-application root.
# Used to suppress false-positive dev-artifact classification (e.g. a "dist"
# folder inside C:/Qt should NOT be treated as the user's build output).
_APP_CONTEXT_SEGS = frozenset({"program files", "program files (x86)"})

# Well-known applications that install to a drive root rather than Program Files.
# Only matched at depth-1 of the path (C:/Qt, C:/MATLAB, not C:/Users/name/Qt).
_STANDALONE_APP_ROOTS = frozenset({
    "qt",                    # Qt Framework  (C:/Qt)
    "matlab",                # MATLAB
    "texlive",               # TeX Live
    "miktex",                # MiKTeX
    "cygwin",  "cygwin64",   # Cygwin
    "msys64",  "msys2",      # MSYS2 / MinGW
    "ghcup",                 # GHCup (Haskell)
    "strawberryperl",        # Strawberry Perl
    "julia",                 # Julia language
    "android",               # Android SDK root
    "openmediavault",        # OMV
    "xampp",                 # XAMPP
    "wamp", "wampserver",    # WampServer
    "laragon",               # Laragon
})


def _is_app_context(parts: frozenset, path_segs: list) -> bool:
    """True when the path lives inside a recognised application installation root.

    Prevents generic folder names like 'dist', 'build', or 'lib' from being
    misclassified as user dev-artifacts when they belong to an installed app.
    """
    # Standard Windows program directories
    if parts & _APP_CONTEXT_SEGS:
        return True
    # Root-level standalone installs: depth-1 only (C:/Qt/…, not C:/Users/x/Qt/…)
    return len(path_segs) >= 3 and path_segs[1] in _STANDALONE_APP_ROOTS


def _pf_app_name(orig_path: str, lower_path: str) -> str:
    """Return the immediate child-folder name under Program Files, preserving original case."""
    orig_segs = orig_path.replace("\\", "/").split("/")
    low_segs  = lower_path.split("/")
    try:
        idx = next(i for i, s in enumerate(low_segs)
                   if s in ("program files", "program files (x86)"))
        if idx + 1 < len(orig_segs):
            return orig_segs[idx + 1]
    except StopIteration:
        pass
    return ""


def _detect_path_ownership(parts: frozenset, lower_path: str) -> tuple:
    """Detect semantic category from path structure.

    Returns (category, reason, semantic_label, owner_confidence)
    or (None, None, None, None) if no ownership detected.
    """

    # ── GPU shader / driver caches ─────────────────────────────────────
    hit = parts & _GPU_CACHE_SEGS
    if hit:
        return "Cache & Temp", f"GPU cache: {next(iter(hit))}", "GPU Shader Cache", "exact"

    # ── Virtual machine containers ─────────────────────────────────────
    hit = parts & _VM_SEGS
    if hit:
        return "Applications", f"virtual machine: {next(iter(hit))}", "Virtual Machine", "exact"

    # ── Android / phone emulators ─────────────────────────────────────
    hit = parts & _EMULATOR_SEGS
    if hit:
        return "Applications", f"emulator: {next(iter(hit))}", "Android Emulator", "probable"

    # ── Conda / Python distribution environments ───────────────────────
    hit = parts & _CONDA_ROOT_SEGS
    if hit:
        return "AI / ML", f"conda environment: {next(iter(hit))}", "Python Environment", "probable"

    # ── Python site-packages ──────────────────────────────────────────
    if "site-packages" in parts:
        return "Dev Artifacts", "Python package directory (site-packages)", "Package Directory", "probable"

    # ── PyInstaller bundled application ────────────────────────────────
    if "_internal" in parts or "_meipass" in parts:
        return "Applications", "PyInstaller bundled application", "Bundled Runtime", "probable"

    # ── AI/ML frameworks ──────────────────────────────────────────────
    hit = parts & _AI_FRAMEWORK_SEGS
    if hit:
        return "AI / ML", f"AI/ML framework: {next(iter(hit))}", "AI Model Storage", "probable"

    # ── .NET runtime ──────────────────────────────────────────────────
    if parts & _DOTNET_SEGS or parts & _DOTNET_COMPONENT_SEGS:
        return "System", ".NET runtime", "Runtime Framework", "exact"

    # ── Java SDK / JRE ────────────────────────────────────────────────
    if parts & _JAVA_SEGS:
        return "Dev Artifacts", "Java runtime/SDK", "Development SDK", "exact"

    # ── Rust/Cargo environment ────────────────────────────────────────
    if ".cargo" in parts:
        return "Dev Artifacts", "Rust/Cargo environment", "Package Registry", "probable"

    # ── Go workspace ─────────────────────────────────────────────────
    if "gopath" in parts or "goroot" in parts:
        return "Dev Artifacts", "Go workspace", "Development Workspace", "probable"

    # ── Flutter/Dart environment ──────────────────────────────────────
    if ".flutter" in parts or (parts & {"flutter", "dart"} and "cache" in parts):
        return "Dev Artifacts", "Flutter/Dart environment", "Development SDK", "probable"

    # ── Docker ───────────────────────────────────────────────────────
    hit = parts & _DOCKER_SEGS
    if hit:
        return "Dev Artifacts", f"Docker container data: {next(iter(hit))}", "Container Data", "probable"

    # ── Chocolatey managed packages ───────────────────────────────────
    if parts & _CHOCO_SEGS:
        return "Applications", "Chocolatey-managed package", "Package Manager", "probable"

    # ── Package manager caches and registries ─────────────────────────
    for mgr, ctx, cat, reason, s_label, s_conf in _PKG_CACHE_PAIRS:
        if mgr in parts and ctx in parts:
            return cat, reason, s_label, s_conf

    # ── Application updater artifacts ─────────────────────────────────
    if parts & _UPDATER_SEGS:
        return "Cache & Temp", "application updater artifacts", "Updater Artifacts", "heuristic"

    # ── Windows sandbox / container images ────────────────────────────
    if "windowssandbox" in parts or "containersandbox" in parts:
        return "Applications", "Windows Sandbox container", "Sandbox Container", "exact"

    # ── Electron app caches ───────────────────────────────────────────
    if ("code-cache" in parts or "blob_storage" in parts or "gpucache" in parts) and \
            any(kw in lower_path for kw in ("electron", "discordapp", "slack", "teams", "zoom")):
        return "Cache & Temp", "Electron app cache", "Application Cache", "heuristic"

    return None, None, None, None


# ═══════════════════════════════════════════════════════════════════
#  PROTECTED PATH DETECTION
# ═══════════════════════════════════════════════════════════════════

# System roots protected ONLY at the drive root (C:/Windows), never when the
# same word appears deeper (a game's .../Saved/Config/Windows is not the OS).
# ProgramData is intentionally absent so its reclaimable vendor data is scanned.
_PROTECTED_ROOT_DIR_NAMES = frozenset({
    "windows", "recovery", "boot",
    "$windows.~bt", "$windows.~ws", "msocache", "perflogs",
    "system volume information", "config.msi",
})

_PROTECTED_APPDATA_DIRS = frozenset({
    "microsoft", "local settings", "credential",
    "identities", "crypto", "protect", "systemcertificates",
})


def _is_protected_path(lower_path: str) -> bool:
    parts = lower_path.rstrip("/").split("/")
    # Only the first directory under the drive letter is a true system root.
    if len(parts) >= 2 and parts[1] in _PROTECTED_ROOT_DIR_NAMES:
        return True
    if "appdata" in parts:
        for part in parts:
            if part in _PROTECTED_APPDATA_DIRS:
                return True
    return False


# ═══════════════════════════════════════════════════════════════════
#  MAIN CATEGORIZATION FUNCTION
#
#  Returns (category, source_rule, semantic_label, owner_confidence).
#
#  owner_confidence — quality of ownership detection:
#    "exact"     strong structural evidence
#    "probable"  good semantic match, not guaranteed
#    "heuristic" weak pattern inference
#    "none"      extension-only or Unknown
# ═══════════════════════════════════════════════════════════════════

def categorize(path: str, name: str, ext: str, is_dir: bool, size_bytes: int = 0) -> tuple:
    lower_path = path.lower().replace("\\", "/")
    lower_name = name.lower()
    lower_ext  = ext.lower()
    segs       = lower_path.split("/")   # list — needed for depth checks
    parts      = frozenset(segs)         # set  — O(1) membership

    # ── 1. Known dev-artifact directory names ────────────────────────
    # Suppressed when the path is inside a recognised application root so
    # that folders like "dist", "lib", or "build" within an installed app
    # (e.g. C:/Qt/dist, C:/Program Files/SomeApp/lib) are not misclassified
    # as the user's own development artifacts.
    if is_dir and lower_name in _DEV_ARTIFACT_NAMES:
        if not _is_app_context(parts, segs):
            s_label, s_conf = _DEV_ARTIFACT_LABELS.get(lower_name, ("Dev Artifact", "exact"))
            return "Dev Artifacts", f"dev artifact directory: {lower_name}", s_label, s_conf
        # Inside an app root — fall through to application detection below

    # ── 1b. Model weights, before anything can call them cache ───────
    # HuggingFace keeps models under ~/.cache/huggingface/, so the cache rule
    # below matched on "cache" and returned first — a 4 GB .gguf was filed as
    # "Cache & Temp" at risk Safe, which is the tier Quick Cleanup bulk-selects.
    # Weights are expensive to re-download, never disposable, and the AI/ML
    # rules further down were unreachable for anything under a cache path.
    if not is_dir:
        if lower_ext in _AI_ML_EXTS:
            return "AI / ML", f"AI/ML model: {lower_ext}", "AI Model", "probable"
        if is_model_blob(lower_name, lower_ext, parts, size_bytes):
            return ("AI / ML", "AI/ML model blob (large, in a model store)",
                    "AI Model", "heuristic")

    # ── 2. Cache & Temp / Browser Data ───────────────────────────────
    for kw in _CACHE_KEYWORDS:
        if kw in lower_path:
            for bkw in _BROWSER_CACHE_KEYWORDS:
                if bkw in lower_path:
                    return "Browser Data", f"browser cache: {bkw}/{kw}", "Browser Cache", "exact"
            # Differentiate cache sub-types for better labels
            if kw == "thumbcache":
                s_label, s_conf = "Thumbnail Cache", "exact"
            elif kw == "__pycache__":
                s_label, s_conf = "Build Cache", "exact"
            elif kw == "cache":
                s_label, s_conf = "Application Cache", "heuristic"
            else:   # temp / tmp
                s_label, s_conf = "Temporary Files", "heuristic"
            return "Cache & Temp", f"cache/temp keyword: {kw}", s_label, s_conf

    # ── 3. Log files ─────────────────────────────────────────────────
    if lower_ext in _LOG_EXTS or lower_name == "logs":
        return "System Logs", f"log: {lower_name}", "Log Files", "exact"

    # ── 4. Path ownership detection (specific patterns) ─────────────
    #  Runs before extension checks: owned runtimes override generic types.
    cat, reason, s_label, s_conf = _detect_path_ownership(parts, lower_path)
    if cat:
        return cat, reason, s_label, s_conf

    # ── 4b. Program Files — installed application (broad fallback) ───
    #  Anything reaching here that lives under Program Files is almost
    #  certainly part of an installed application.  More-specific patterns
    #  above (conda, .NET, VM, etc.) already returned if they matched.
    if parts & _APP_CONTEXT_SEGS:
        app_name = _pf_app_name(path, lower_path)
        label    = app_name if app_name else "Application Component"
        return "Applications", f"installed in Program Files: {label}", label, "probable"

    # ── 4c. Standalone app root at drive root (C:/Qt, C:/MATLAB, …) ─
    #  Depth-1 only: must be the very first directory under the drive
    #  letter to avoid catching user folders named after these tools.
    if len(segs) >= 2 and segs[1] in _STANDALONE_APP_ROOTS:
        orig_segs = path.replace("\\", "/").split("/")
        app_name  = orig_segs[1] if len(orig_segs) > 1 else segs[1]
        return "Applications", f"standalone app root: {app_name}", app_name, "probable"

    # ── 5a. Application binaries ─────────────────────────────────────
    if lower_ext in _APP_EXTS:
        return "Applications", f"application binary: {lower_ext}", "Application Binary", "heuristic"

    # ── 5b. Archives ─────────────────────────────────────────────────
    if lower_ext in _ARCHIVE_EXTS:
        return "Archives", f"archive: {lower_ext}", "Archive", "heuristic"

    # ── 5c. Media — split into Images / Videos / Audio (mode parity) ──
    if lower_ext in _IMAGE_EXTS:
        return "Images", f"image file: {lower_ext}", "Image", "heuristic"
    if lower_ext in _VIDEO_EXTS:
        return "Videos", f"video file: {lower_ext}", "Video", "heuristic"
    if lower_ext in _AUDIO_EXTS:
        return "Audio", f"audio file: {lower_ext}", "Audio", "heuristic"

    # ── 5d. Documents ────────────────────────────────────────────────
    if lower_ext in _DOC_EXTS:
        return "Documents", f"document: {lower_ext}", "Document", "heuristic"

    # ── 5e. Databases & Saves ────────────────────────────────────────
    if lower_ext in _DB_EXTS:
        return "Databases & Saves", f"database: {lower_ext}", "Database", "heuristic"

    # ── 5f. AI / ML models ───────────────────────────────────────────
    if lower_ext in _AI_ML_EXTS:
        return "AI / ML", f"AI/ML model: {lower_ext}", "AI Model", "probable"
    if lower_ext in _AI_ML_LARGE_EXTS:
        if size_bytes > 50 * 1024 * 1024 or (parts & _AI_ML_PATH_KEYWORDS):
            return "AI / ML", f"AI/ML model (large/path): {lower_ext}", "AI Model", "heuristic"
    if is_model_blob(lower_name, lower_ext, parts, size_bytes):
        return "AI / ML", "AI/ML model blob (large, in a model store)", "AI Model", "heuristic"

    # ── 5g. System files ─────────────────────────────────────────────
    if lower_ext in _SYSTEM_EXTS:
        return "System", f"system file: {lower_ext}", "System File", "heuristic"

    # ── 5h. Source code ──────────────────────────────────────────────
    if lower_ext in _SOURCE_EXTS:
        return "Dev Artifacts", f"source code: {lower_ext}", "Source Code", "heuristic"

    return "Unknown", "no rule matched", "", "none"


# ═══════════════════════════════════════════════════════════════════
#  RISK ASSIGNMENT
# ═══════════════════════════════════════════════════════════════════

def assign_risk(category: str, path: str, size_bytes: int) -> tuple:
    lower = path.lower().replace("\\", "/")

    if _is_protected_path(lower):
        return "Protected", "system-critical or protected path"

    if category in ("Cache & Temp", "Browser Data", "System Logs"):
        return "Safe", f"{category} — auto-regenerated"

    if category == "Dev Artifacts":
        ext = os.path.splitext(path)[1].lower()
        if ext in _SOURCE_EXTS:
            return "Review", "source code — user-written, verify before removing"
        return "Safe", "dev artifact — regenerated by build tools"

    if category == "Applications":
        return "Review", "application files — verify they are no longer needed"

    if category in ("Documents", "Media", "Images", "Videos", "Audio", "Archives"):
        if category == "Archives":
            return "Optional", "archive file — usually removable if no longer needed"
        return "Review", f"{category} — may contain user data"

    if category == "Databases & Saves":
        return "Review", "database or save file — may contain user data"

    if category == "AI / ML":
        return "Review", "AI model or environment — safe to remove if not needed"

    if category == "System":
        return "Review", "system file — verify before removing"

    if category == "Unknown" and size_bytes >= 100 * 1024 * 1024:
        return "Review", f"unknown large file ({_format_size(size_bytes)})"

    return "Review", "uncategorized item"


# ═══════════════════════════════════════════════════════════════════
#  FINDING DATACLASS
# ═══════════════════════════════════════════════════════════════════

@dataclass(slots=True)
class Finding:
    """A single file/folder finding from a scan.

    slots=True is load-bearing, not tidiness: a full C:/ scan holds ~1.8M of
    these live at once, and the per-instance __dict__ alone cost ~256 bytes
    each (~440 MB) before the field values themselves. Every attribute written
    anywhere (ai_status, ai_explanation, …) must stay a declared field below —
    slots make a stray attribute an AttributeError rather than silent bloat.
    """
    path: str
    name: str
    is_dir: bool
    size_bytes: int
    extension: str
    modified: float
    accessed: float
    parent: str

    # A subtree that was measured but deliberately not walked. .git is
    # recorded so the detector can recognise a project, and not descended
    # because its object store is thousands of files nobody triages — but
    # those bytes are still on the disk and still go when the folder is
    # recycled. Without them the row under-reported: client-field-app showed
    # 3.89 GB and recycling it took 5.58 GB.
    #
    # Two slots, weighed against the memory note above: ~16 bytes on each of
    # ~1.8M findings in a full C:/ scan, ~29 MB worst case. That is the price
    # of a row that means what the button does.
    undescended_bytes: int = 0
    undescended_files: int = 0

    category: str = ""
    risk: str = ""
    source_rule: str = ""
    risk_reason: str = ""

    # Semantic enrichment — populated by categorize()
    semantic_label: str = ""       # human-readable sub-type label
    owner_confidence: str = "none" # exact | probable | heuristic | none

    size: str = ""
    age: str = ""

    # True when the file is a cloud placeholder (OneDrive "files on-demand",
    # offline/dehydrated). Its bytes are NOT stored on this disk, so
    # size_bytes is 0 and the on-disk total stays honest.
    cloud_only: bool = False

    ai_status: str = "none"
    ai_explanation: str = ""
    ai_error: str = ""
    ai_model: str = ""
    ai_language: str = ""
    ai_updated_at: float = 0.0

    def __post_init__(self):
        if not self.category:
            result = categorize(
                self.path, self.name, self.extension, self.is_dir, self.size_bytes
            )
            self.category, self.source_rule, self.semantic_label, self.owner_confidence = result
        if not self.risk:
            self.risk, self.risk_reason = assign_risk(
                self.category, self.path, self.size_bytes
            )
        if not self.size:
            if self.cloud_only:
                from app.i18n import tr
                self.size = tr("⇧ cloud-only")
            else:
                self.size = _format_size(self.size_bytes)
        if not self.age:
            self.age = _format_age(self.modified)

    @property
    def cache_key(self) -> str:
        norm = self.path.replace("\\", "/").lower()
        return f"{norm}|{self.size_bytes}|{int(self.modified)}"

    @staticmethod
    def _safe_date(ts: float) -> str:
        try:
            if ts <= 0:
                return "—"
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        except (OSError, ValueError, OverflowError):
            return "—"

    def to_dict(self) -> dict:
        from app.models.risk import reclaimable_bytes
        reclaimable = reclaimable_bytes(self.risk, self.size_bytes)
        return {
            "category":        self.category,
            "semantic_label":  self.semantic_label,
            "owner_confidence":self.owner_confidence,
            "path":            self.path,
            "name":            self.name,
            "is_dir":          self.is_dir,
            "size":            self.size,
            "size_bytes":      self.size_bytes,
            "cloud_only":      self.cloud_only,
            "reclaimable_bytes": reclaimable,
            "age":             self.age,
            # Raw mtime, not just the formatted `age`/`first_seen` strings.
            # The findings table sorts on this key; without it every row fell
            # back to the same default and "Oldest first" silently did nothing.
            # Session restore also needs it to rebuild ages after a resume.
            "modified":        self.modified,
            # Raw atime alongside the formatted `last_access`. The session
            # store persists this one and re-derives the string, so a restored
            # finding keeps its real last-access date instead of showing "—".
            "accessed":        self.accessed,
            "risk":            self.risk,
            "source_rule":     self.source_rule,
            "risk_reason":     self.risk_reason,
            "why":             self._why_text(),
            "last_access":     self._safe_date(self.accessed),
            "first_seen":      self._safe_date(self.modified),
            "recommendation":  self._recommendation(),
            "ai_status":       self.ai_status,
            "ai_explanation":  self.ai_explanation,
            "ai_error":        self.ai_error,
            "ai_model":        self.ai_model,
            "ai_language":     self.ai_language,
        }

    def _why_text(self) -> str:
        """Plain-language explanation, consistent with SmartEntity._why_text."""
        what = self.semantic_label or self.category or "File"
        reason = (self.risk_reason or "").strip().rstrip(".")
        if reason:
            return f"What it is: {what}. Why flagged: {reason}."
        return f"What it is: {what}."

    def _recommendation(self) -> str:
        # Wording mirrors SmartEntity._recommendation so the two scan modes
        # never contradict each other for the same risk level.
        if self.risk == "Protected":
            return "Do not remove — system-critical or protected"
        if self.risk == "Review":
            return "Review before cleanup — may still matter to you or an app"
        if self.risk == "Optional":
            return "Optional cleanup — remove if you no longer need it"
        if self.risk == "Safe":
            if self.category in ("Cache & Temp", "Browser Data"):
                return "Remove — auto-regenerated"
            if self.category == "Dev Artifacts":
                return "Safe cleanup — regenerated by build tools"
            if self.category == "System Logs":
                return "Remove — old logs"
            return "Likely safe to remove"
        return "Manual review required"
