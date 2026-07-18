"""Curated rules for well-known Windows storage locations.

Software stores things in conventional places. Where that convention is
unambiguous, a rule states plainly what lives there — which is faster and far
more reliable than inferring it from file extensions, and it removes whole
folders from the "Unknown" pile.

Scope and limits, deliberately:

* Rules **annotate**; they never contradict verified evidence. They are applied
  after detection and only replace a *generic* classification, so a pass that
  identified something more specific keeps its answer.
* Only conventions that are stable across machines are listed. Anything that
  varies per install belongs in the pattern rules, not here.
* A rule may make an item **more** protected. It should never make something
  look safer than the evidence supports — the risks below are conservative:
  ``Package Cache`` is Optional rather than Safe because removing it makes
  Windows re-download installers before it can repair or modify a program.
"""
from __future__ import annotations

import os

_LOCAL = (os.environ.get("LOCALAPPDATA") or "").replace("\\", "/").lower()
_ROAM = (os.environ.get("APPDATA") or "").replace("\\", "/").lower()
_PDATA = (os.environ.get("ProgramData") or "").replace("\\", "/").lower()
_HOME = (os.environ.get("USERPROFILE") or "").replace("\\", "/").lower()


def _norm(path: str) -> str:
    return path.replace("\\", "/").rstrip("/").lower()


# Each rule: exact folder path → what it is.
#   type / name / risk / reason are applied to the matching entity.
# Built from environment roots so the table stays portable across machines.
def _rules() -> list[dict]:
    r: list[dict] = []

    def add(path, type_, name, risk, reason):
        if path and not path.startswith("/"):
            r.append({"path": _norm(path), "type": type_, "name": name,
                      "risk": risk, "reason": reason})

    if _LOCAL:
        add(f"{_LOCAL}/microsoft/windows/explorer", "cache_folder",
            "Thumbnail & icon cache", "Safe",
            "Windows thumbnail and icon cache — rebuilt automatically the next "
            "time you browse folders")
        add(f"{_LOCAL}/microsoft/windows/inetcache", "cache_folder",
            "Internet file cache", "Safe",
            "Temporary internet files — regenerated as you browse")
        add(f"{_LOCAL}/crashdumps", "log_folder", "Crash dumps", "Safe",
            "Crash dumps kept for troubleshooting — safe to clear")
        add(f"{_LOCAL}/d3dscache", "shader_cache", "DirectX shader cache", "Safe",
            "DirectX shader cache — rebuilt by games automatically")
        add(f"{_LOCAL}/pip/cache", "dev_artifacts", "pip download cache", "Safe",
            "Python package download cache — re-downloaded on demand")
        add(f"{_LOCAL}/yarn/cache", "dev_artifacts", "Yarn cache", "Safe",
            "Yarn package cache — restored by a reinstall")
    if _ROAM:
        add(f"{_ROAM}/npm-cache", "dev_artifacts", "npm cache", "Safe",
            "npm package cache — restored by a reinstall")
    if _PDATA:
        # NOT Safe: Windows uses this to repair/modify installed programs.
        add(f"{_PDATA}/package cache", "installer_cache", "Installer cache",
            "Optional",
            "Installer payloads Windows keeps to repair or modify programs — "
            "removing them means the installers are downloaded again if needed")
    if _HOME:
        add(f"{_HOME}/.nuget/packages", "dev_artifacts", "NuGet package cache",
            "Optional", "NuGet packages — restored on the next build")
        add(f"{_HOME}/.m2/repository", "dev_artifacts", "Maven repository",
            "Optional", "Maven artifacts — re-downloaded on the next build")
        add(f"{_HOME}/.gradle/caches", "dev_artifacts", "Gradle cache",
            "Optional", "Gradle build cache — rebuilt on the next build")
        add(f"{_HOME}/.cargo/registry", "dev_artifacts", "Cargo registry cache",
            "Optional", "Rust crate cache — re-downloaded on the next build")

    # Vendor folders Windows installs on its own. The user never chose these,
    # and removing them breaks OS features, so they are Protected rather than
    # presented as a large deletable "Microsoft" folder.
    for pf in ("c:/program files/microsoft", "c:/program files (x86)/microsoft"):
        r.append({"path": pf, "type": "application", "name": "Microsoft (Windows components)",
                  "risk": "Protected",
                  "reason": "Windows-installed components such as Edge and Copilot — "
                            "installed by Windows, not by you; removing them breaks OS features"})
    return r


_CACHE: list[dict] | None = None


def rules() -> list[dict]:
    global _CACHE
    if _CACHE is None:
        _CACHE = _rules()
    return _CACHE


def lookup(path: str) -> dict | None:
    """Return the curated rule for *path*, or None."""
    target = _norm(path)
    for rule in rules():
        if target == rule["path"]:
            return rule
    return None


# Classifications considered generic enough for a curated rule to replace.
_GENERIC_TYPES = {
    "unknown_folder", "mixed_folder", "document_folder", "database",
    "application_data", "loose_files", "archive_group", "dataset",
}


def apply_known_path_rules(entities: list) -> int:
    """Apply curated rules to matching entities. Returns how many changed.

    A rule replaces the *label* of a generically-classified entity, and may
    always raise protection. It will not talk a specific classification down.
    """
    from app.models.risk import risk_sort_index

    changed = 0
    for e in entities:
        rule = lookup(e.path)
        if not rule:
            continue
        specific = e.entity_type not in _GENERIC_TYPES
        # A rule may always make an item MORE cautious — that direction is safe
        # and is often the point (Package Cache is classified Safe by content,
        # but removing it costs you offline repair, so the rule raises it to
        # Optional). Relabelling a specific type is only allowed when the rule
        # is not lowering caution.
        more_cautious = risk_sort_index(rule["risk"]) > risk_sort_index(e.risk)
        if specific and not more_cautious:
            continue
        e.entity_type = rule["type"]
        e.name = rule["name"]
        e.risk = rule["risk"]
        e.risk_reason = rule["reason"]
        e.summary = rule["reason"]
        changed += 1
    return changed
