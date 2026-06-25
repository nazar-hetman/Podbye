"""Cloud-sync path detection.

Pass 1 — Path-based: expand known provider root patterns under %USERPROFILE%.
Pass 2 — Attribute-based: flag any path with FILE_ATTRIBUTE_REPARSE_POINT as a
          cloud placeholder (Files-On-Demand, etc.). Fails safe on any error.

Usage:
    roots = detect_cloud_roots()          # {norm_path: provider_name}
    provider = is_cloud_path(path, roots) # str or None
"""
from __future__ import annotations

import os
import sys

# ── Provider patterns ──────────────────────────────────────────────

_CLOUD_PATTERNS: list[tuple[str, str]] = [
    ("OneDrive",       "OneDrive"),
    ("Dropbox",        "Dropbox"),
    ("Google Drive",   "Google Drive"),
    ("GoogleDrive",    "Google Drive"),
    ("iCloudDrive",    "iCloud"),
    ("Box",            "Box"),
    ("pCloud Drive",   "pCloud"),
    ("Mega",           "MEGA"),
]

_ONEDRIVE_BUSINESS_PREFIX = "OneDrive - "   # OneDrive for Business

# Windows FILE_ATTRIBUTE_REPARSE_POINT
_FILE_ATTR_REPARSE_POINT = 0x400


# ── Detection API ──────────────────────────────────────────────────

def detect_cloud_roots() -> dict[str, str]:
    """Return {normalized_path: provider_name} for all cloud-sync roots found.

    Windows-only. Returns empty dict on other platforms or on permission errors.
    """
    if sys.platform != "win32":
        return {}

    user_profile = os.environ.get("USERPROFILE", "")
    if not user_profile:
        return {}

    try:
        entries = os.listdir(user_profile)
    except OSError:
        return {}

    roots: dict[str, str] = {}
    for entry in entries:
        full = os.path.join(user_profile, entry)
        if not os.path.isdir(full):
            continue

        provider = _match_provider(entry)
        if provider:
            norm = _norm(full)
            roots[norm] = provider

    return roots


def is_cloud_path(path: str, cloud_roots: dict[str, str]) -> str | None:
    """Return provider name if path is inside a cloud-sync root, else None."""
    if not cloud_roots:
        return None
    n = _norm(path)
    for root, provider in cloud_roots.items():
        if n == root or n.startswith(root + "/"):
            return provider
    return None


def is_cloud_placeholder(path: str) -> bool:
    """True if the file/dir has the reparse-point attribute set (cloud placeholder).

    Fails safe — returns False on any error.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        attrs = ctypes.windll.kernel32.GetFileAttributesW(path)
        if attrs == 0xFFFFFFFF:
            return False
        return bool(attrs & _FILE_ATTR_REPARSE_POINT)
    except Exception:
        return False


# ── Internal helpers ───────────────────────────────────────────────

def _norm(path: str) -> str:
    return path.replace("\\", "/").lower().rstrip("/")


def _match_provider(entry: str) -> str | None:
    lower = entry.lower()
    for pattern, provider in _CLOUD_PATTERNS:
        if lower == pattern.lower():
            return provider
    if entry.startswith(_ONEDRIVE_BUSINESS_PREFIX):
        return "OneDrive"
    return None
