"""Is the application that owns this data still on the machine?

Deliberately tri-state, and deliberately unable to say "no".

Measured on a real profile: matching a support-folder name against the Windows
uninstall registry mislabels most apps — ``.vscode`` (2.8 GB) and ``.lmstudio``
(2.65 GB) are absent from it although both apps are installed, and 7 of 11
probed apps would have been called "orphaned, safe to delete". Modern software
installs per-user, as MSIX, portable, or via pip/npm/winget, and much of it
leaves no uninstall entry at all.

So this module answers ``PRESENT`` (positive evidence found) or ``UNKNOWN``
(none found). It never answers "absent": tools like the AWS CLI, Azure CLI and
Gemini CLI leave no discoverable footprint, so absence of evidence is not
evidence of absence. Callers must treat UNKNOWN as "ask the user", never as
"safe to delete".
"""
from __future__ import annotations

import glob
import os
import re

PRESENT = "present"
UNKNOWN = "unknown"
GENERIC = "generic"   # not an application name at all (.cache, .config, …)

# Folder names that are conventions rather than products. Asking whether
# ".cache" is installed is meaningless, and loose matching happily "finds" it.
_GENERIC_DATA_DIRS = {
    "cache", "config", "local", "share", "tmp", "temp", "data", "logs",
    "state", "bin", "lib", "ssh", "gnupg", "continuum", "conda",
}

# Support-folder name → the product name as it appears on the system. This is
# where a curated list genuinely earns its keep: it is what makes ".vscode"
# resolvable to "Visual Studio Code" instead of being called orphaned.
_ALIASES: dict[str, tuple[str, ...]] = {
    "vscode": ("visual studio code", "code"),
    "vscode-shared": ("visual studio code", "code"),
    "lmstudio": ("lm studio",),
    "ollama": ("ollama",),
    "docker": ("docker desktop", "docker"),
    "dotnet": (".net", "dotnet"),
    "nuget": ("nuget", "visual studio"),
    "m2": ("maven", "java"),
    "gradle": ("gradle", "android studio"),
    "android": ("android studio", "android debug bridge"),
    "virtualbox": ("oracle vm virtualbox", "virtualbox"),
    "memuhyperv": ("memu", "microvirt"),
    "vs": ("visual studio",),
    "idea": ("intellij idea",),
    "pycharm": ("pycharm",),
    "nvm": ("nvm for windows", "node.js"),
    "npm": ("node.js", "npm"),
    "yarn": ("yarn", "node.js"),
    "cargo": ("rust", "cargo"),
    "rustup": ("rust", "rustup"),
    "codeium": ("codeium", "windsurf"),
    "windsurf": ("windsurf",),
    "claude": ("claude",),
    "copilot": ("copilot", "github copilot"),
    "aws": ("aws command line interface", "aws cli"),
    "azure": ("microsoft azure cli", "azure cli"),
    "gcloud": ("google cloud sdk",),
    "node-red": ("node-red", "node.js"),
    "matplotlib": ("python",),
    "astropy": ("python",),
    "designer": ("qt", "qt designer"),
}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


# ── Evidence sources ────────────────────────────────────────────────
# Each returns a set of normalized product names found on the machine. They are
# gathered once and cached: enumerating the Start Menu and PATH is slow, and a
# scan asks about hundreds of folders.

_CACHE: dict[str, set[str]] | None = None


def _dir_names(path: str) -> set[str]:
    try:
        return {_norm(n) for n in os.listdir(path)
                if os.path.isdir(os.path.join(path, n))}
    except OSError:
        return set()


def _registry_names() -> set[str]:
    try:
        from app.services.entity_detector import _get_installed_programs
        return {_norm(v.get("name", "")) for v in _get_installed_programs().values()}
    except Exception:
        return set()


def _program_dir_names() -> set[str]:
    local = os.environ.get("LOCALAPPDATA", "")
    out = _dir_names(r"C:\Program Files") | _dir_names(r"C:\Program Files (x86)")
    if local:
        out |= _dir_names(os.path.join(local, "Programs"))
    return out


def _start_menu_names() -> set[str]:
    out: set[str] = set()
    for base in (os.environ.get("APPDATA", ""), os.environ.get("ProgramData", "")):
        if not base:
            continue
        root = os.path.join(base, "Microsoft", "Windows", "Start Menu", "Programs")
        try:
            for lnk in glob.glob(os.path.join(root, "**", "*.lnk"), recursive=True):
                out.add(_norm(os.path.splitext(os.path.basename(lnk))[0]))
        except OSError:
            pass
    return out


def _path_executables() -> set[str]:
    out: set[str] = set()
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if not d:
            continue
        try:
            for n in os.listdir(d):
                if n.lower().endswith((".exe", ".cmd", ".bat")):
                    out.add(_norm(os.path.splitext(n)[0]))
        except OSError:
            pass
    return out


def _running_processes() -> set[str]:
    try:
        import psutil
        return {_norm(os.path.splitext(p.info.get("name") or "")[0])
                for p in psutil.process_iter(["name"])}
    except Exception:
        return set()


_SOURCES = (
    ("registry", _registry_names),
    ("installed folder", _program_dir_names),
    ("Start Menu", _start_menu_names),
    ("PATH", _path_executables),
    ("running process", _running_processes),
)


def evidence(force_refresh: bool = False) -> dict[str, set[str]]:
    """Gather (and cache) every evidence source."""
    global _CACHE
    if _CACHE is None or force_refresh:
        _CACHE = {label: fn() for label, fn in _SOURCES}
    return _CACHE


def reset_cache() -> None:
    global _CACHE
    _CACHE = None


# Sources that identify an installed product rather than merely a command that
# happens to share a name. PATH and running processes are useful corroboration
# but are full of generic short names, so a decision that changes how a folder
# is grouped should not rest on them alone.
STRONG_SOURCES = ("registry", "installed folder", "Start Menu")


def _matches(candidate: str, pool: set[str]) -> bool:
    """Exact match, or a pool entry that EXTENDS the candidate.

    Only that one direction. "visualstudiocode" should match a Start Menu entry
    "visualstudiocodeuser" (a version/edition suffix), but the reverse —
    a short pool entry matching a longer folder name — produced nonsense:
    "archive" matched the tool "arch", and "work" matched "workfolders". That is
    harmless when the answer is only "keep this data", but it also decides
    whether a folder stays whole, where a wrong yes hides a diverse folder's
    contents.
    """
    if len(candidate) < 3:
        return False
    for name in pool:
        if len(name) < 3:
            continue
        if name == candidate or name.startswith(candidate):
            return True
    return False


def presence(folder_name: str, strong_only: bool = False) -> tuple[str, str]:
    """Return (state, evidence_label) for the app owning *folder_name*.

    state is PRESENT, GENERIC, or UNKNOWN — never "absent". A leading dot is
    stripped, so ".vscode" and "vscode" behave the same.

    *strong_only* restricts the answer to sources that identify an installed
    product (registry / installed folder / Start Menu). Use it where a wrong
    "present" would change grouping rather than just wording.
    """
    raw = (folder_name or "").lstrip(".").strip()
    key = _norm(raw)
    if not key:
        return UNKNOWN, ""
    if key in {_norm(g) for g in _GENERIC_DATA_DIRS}:
        return GENERIC, ""

    candidates = [key] + [_norm(a) for a in _ALIASES.get(raw.lower(), ())]
    ev = evidence()
    for label, _ in _SOURCES:
        if strong_only and label not in STRONG_SOURCES:
            continue
        pool = ev.get(label, set())
        for cand in candidates:
            if _matches(cand, pool):
                return PRESENT, label
    return UNKNOWN, ""


def describe(folder_name: str) -> str:
    """A sentence for the UI that never overstates what we know."""
    state, source = presence(folder_name)
    name = (folder_name or "").lstrip(".")
    if state == GENERIC:
        return "Shared support folder — not tied to a single application"
    if state == PRESENT:
        return f"{name} appears to be installed (found in {source}) — keep this data"
    return (f"Could not confirm whether {name} is still installed — "
            "check before removing")
