"""Windows startup entry detector.

Reads from:
  1. Registry Run keys (HKCU + HKLM + WOW64 variant)
  2. StartupApproved keys for accurate enabled/disabled state
  3. User and common startup folders (.lnk shortcuts)

Returns a list of StartupEntry objects, deduplicated and risk-classified.
"""
from __future__ import annotations

import ctypes
import os
import re
import struct
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from app.models.startup_entry import StartupEntry


# ── Risk keyword sets ─────────────────────────────────────────────
# Matched against lower(name) — order of evaluation: Protected → Optional → Review → Safe

_PROTECTED_NAME_KEYS = {
    "ctfmon", "windefend", "securityhealth", "securityhealthsystray",
    "msseces", "msmpeng", "narrator", "magnify",
    "windows defender", "windows security",
}

_SECURITY_NAME_KEYS = {
    # Antivirus / endpoint security — disabling = unprotected
    "mcafee", "norton", "avast", "avira", "kaspersky", "bitdefender",
    "malwarebytes", "mbam", "sophos", "eset ", "trend micro", "webroot",
    "cylance", "comodo", "crowdstrike", "sentinel one", "panda",
    # VPN — disabling = connection unprotected
    "nordvpn", "expressvpn", "cyberghost", "mullvad", "windscribe",
    "protonvpn", "private internet", "tunnelbear", "hotspot shield",
}

_HARDWARE_NAME_KEYS = {
    "asus", "armoury crate", "myasus", "gigabyte", "msi center", "lenovo vantage",
    "dell optimizer", "hp system event", "corsair", "logitech options", "razer",
    "nvidia container", "nvcplui", "nvdisplay", "nvtmru",
    "amd settings", "amdow", "amdupdateservice",
    "realtek", "conexant", "idt pc audio", "synaptics",
    "intel display", "intel hd graphics",
}

_OPTIONAL_NAME_KEYS = {
    # Cloud sync — sync stops if disabled
    "onedrive", "dropbox", "google drive", "googledrivesync", "googledrive",
    "icloud", "box sync", "boxdrive", "megasync", "nextcloud",
    # Game launchers — games won't auto-open
    "steam", "epic games", "epicgameslauncher", "origin", "ea desktop",
    "uplay", "ubisoft connect", "gog galaxy", "battlenet", "riot client",
    "xbox", "gameoverlaui",
    # Update helpers — app updates won't run automatically
    "update", "updater", "upgrade", "autoupdate",
    "zoom", "skype", "teams", "office", "acrobat",
    # Browser helpers
    "chrome update", "firefox update",
    # General agents / daemons that may have delayed impact
    "agent", "daemon", "helper",
}

_SAFE_NAME_KEYS = {
    "discord", "spotify", "telegram", "signal", "whatsapp",
    "slack", "gimp", "qbittorrent", "vlc", "7-zip", "itunes",
    "ditto", "greenshot", "sharex", "notion", "obsidian",
    "steam client", "rainmeter",
}

_REMOTE_ACCESS_KEYS = {
    "tailscale", "teamviewer", "anydesk", "parsec",
}

_KNOWN_PUBLISHERS = {
    "asus": "ASUS",
    "armoury": "ASUS",
    "myasus": "ASUS",
    "figma": "Figma",
    "docker": "Docker",
    "tailscale": "Tailscale",
    "grammarly": "Grammarly",
    "microsoft": "Microsoft",
    "onedrive": "Microsoft",
    "teams": "Microsoft",
    "msteams": "Microsoft",
    "office": "Microsoft",
    "windows": "Microsoft",
    "nvidia": "NVIDIA",
    "amd": "AMD",
    "intel": "Intel",
    "realtek": "Realtek",
    "synaptics": "Synaptics",
    "discord": "Discord",
    "spotify": "Spotify",
    "slack": "Slack",
    "telegram": "Telegram",
    "signal": "Signal",
    "whatsapp": "WhatsApp",
    "steam": "Valve",
    "epic": "Epic Games",
    "riot": "Riot Games",
    "ubisoft": "Ubisoft",
    "gog": "GOG",
    "dropbox": "Dropbox",
    "google": "Google",
    "chrome": "Google",
    "zoom": "Zoom",
    "skype": "Microsoft",
    "adobe": "Adobe",
    "obsidian": "Obsidian",
}


# Words inside a run of letters/digits: an all-caps prefix, a Capitalised word,
# or a lowercase/numeric run. Splits "MSTeams" into MS + Teams.
_CAMEL_WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+")
_SEPARATORS = re.compile(r"[^A-Za-z0-9]+")


def _search_space(*parts: str) -> str:
    """Space-padded word forms of *parts*, for whole-word keyword matching.

    Keywords used to be matched as bare substrings, which quietly mis-filed
    anything whose name merely contained one:

        'steam'  in 'msteams'   -> Teams became a Game launcher
        'box'    in 'xbox'      -> Xbox became Background sync
        'riot'   in 'patriot'   -> an RGB utility became a Game launcher
        'origin' in 'originals' -> anything under an originals/ folder likewise

    The role decides the risk tier, the boot-impact badge, the recommendation
    and the text handed to the AI, so a wrong role is wrong four times over.

    Each run of letters/digits contributes its CamelCase sub-words and every
    join of *consecutive* sub-words, so concatenated registry names keep
    matching: "GoogleUpdate" yields 'update', and "OneDriveSetup" yields
    'onedrive' as well as 'one', 'drive' and 'setup'. Only sequences that
    really are adjacent in the name are produced, so 'patriot' still yields
    nothing that answers to 'riot'.
    """
    # The sub-words stay in their original order in one sequence, so phrase
    # keys like "google drive" can match across adjacent words. The run-on
    # joins are appended after it rather than inline, where they would break
    # that adjacency apart.
    sequence: list[str] = []
    joins: list[str] = []
    for part in parts:
        for run in _SEPARATORS.split(part or ""):
            if not run:
                continue
            pieces = [p.lower() for p in _CAMEL_WORD.findall(run)] or [run.lower()]
            sequence.extend(pieces)
            for start in range(len(pieces)):
                for end in range(start + 2, len(pieces) + 1):
                    joins.append("".join(pieces[start:end]))
    return f" {' '.join(sequence + joins)} "


def _as_key(keyword: str) -> str:
    """Normalise a keyword the same way, so '7-zip' matches '7-Zip'."""
    return " ".join(w for w in _SEPARATORS.split(keyword.lower()) if w)


def _contains_any(space: str, keywords) -> bool:
    """True when any keyword appears as a whole word (or phrase) in *space*.

    *space* must come from _search_space(); passing a raw string silently
    reverts to the substring behaviour this replaced.
    """
    return any(f" {_as_key(k)} " in space for k in keywords)


_SYNC_KEYS = ("onedrive", "dropbox", "google drive", "googledrive", "icloud",
              "box sync", "boxdrive", "mega", "megasync", "nextcloud", "sync")
_GAME_KEYS = ("steam", "epic", "epic games", "origin", "uplay", "ubisoft",
              "gog", "battlenet", "battle net", "riot", "xbox")
_CHAT_KEYS = ("discord", "teams", "msteams", "slack", "skype", "telegram",
              "whatsapp", "zoom")
_CREATIVE_KEYS = ("figma", "grammarly", "adobe", "creative cloud")
_UPDATE_KEYS = ("update", "updater", "upgrade", "acrobat", "office")
_LIGHT_KEYS = ("ditto", "greenshot", "sharex", "rainmeter", "notion",
               "obsidian", "spotify", "vlc", "7-zip")


def _infer_role(name: str, path: str, publisher: str, product_name: str) -> str:
    space = _search_space(name, path or "", publisher or "", product_name or "")

    if _contains_any(space, _PROTECTED_NAME_KEYS | _SECURITY_NAME_KEYS):
        return "Security component"
    if _contains_any(space, _HARDWARE_NAME_KEYS):
        return "Hardware utility"
    if _contains_any(space, _REMOTE_ACCESS_KEYS):
        return "Remote access service"
    # Chat before games: "Xbox" used to be caught by the sync branch's bare
    # "box", and the one entry that really is a game launcher never reached
    # the game branch at all.
    if _contains_any(space, _CHAT_KEYS):
        return "Communication app"
    if _contains_any(space, _SYNC_KEYS):
        return "Background sync"
    if _contains_any(space, _GAME_KEYS):
        return "Game launcher"
    if _contains_any(space, _CREATIVE_KEYS):
        return "Creative helper"
    if _contains_any(space, _UPDATE_KEYS):
        return "Update helper"
    if _contains_any(space, _LIGHT_KEYS):
        return "Light utility"
    return "Startup item"


def _classify_risk(name: str, path: str, publisher: str, product_name: str) -> tuple[str, str]:
    """Return (recommendation_level, reason) for a startup entry."""
    lo_path = (path or "").lower()
    name_space = _search_space(name)
    role = _infer_role(name, path, publisher, product_name)

    # Protected: Microsoft system component running from system dirs
    is_msft = _contains_any(_search_space(publisher or "", name), ("microsoft",))
    is_syspath = "system32" in lo_path or "syswow64" in lo_path
    # Review, not Protected. In Findings, Protected is enforceable: the item
    # cannot be selected and the button is disabled. Here it never meant that
    # — Podbye has no registry-write capability at all, so it changes no
    # startup entry, and the strongest thing it can honestly say about any of
    # them is "look at this carefully before you change it in Task Manager".
    # Borrowing a word that means "we will refuse" for a screen where nothing
    # is refused taught users the wrong thing about the one that does.
    #
    # The reasons are unchanged, and they are what actually carries the
    # warning.
    if is_msft and is_syspath:
        return "Review", "Windows system component — leave managed by Windows"
    if _contains_any(name_space, _PROTECTED_NAME_KEYS):
        return "Review", "Windows security component — leaving it enabled is recommended"
    if role == "Security component":
        return "Review", "Security component — disabling can reduce active protection"
    if role == "Hardware utility":
        return "Review", "Hardware-sensitive utility — manages device or driver features"

    # Review: unknown publisher or unusual location
    suspicious_dirs = ("\\temp\\", "\\tmp\\", "\\downloads\\", "\\desktop\\", "\\recycle.bin\\")
    if any(seg in lo_path for seg in suspicious_dirs):
        return "Review", "Starts from an unusual location — verify before changing it"
    if not publisher:
        return "Review", "Publisher could not be verified — review before changing it"

    # Optional
    if role in {
        "Background sync",
        "Remote access service",
        "Communication app",
        "Game launcher",
        "Creative helper",
        "Update helper",
    } or _contains_any(name_space, _OPTIONAL_NAME_KEYS) or _contains_any(name_space, _REMOTE_ACCESS_KEYS):
        return "Optional", "Useful convenience startup — can be opened manually when needed"

    # Safe
    if _contains_any(name_space, _SAFE_NAME_KEYS) or role == "Light utility":
        return "Safe", "Convenience utility — disabling only affects automatic launch"

    # Default: review (unknown)
    return "Review", "Purpose is not fully clear — review before changing it"


# The reasons _classify_risk gives an entry that Windows or a device vendor
# owns. They are all Review — Podbye modifies no startup entry — but the
# advice is already exact and a small model adds nothing to it.
_SYSTEM_MANAGED_REASONS = frozenset({
    "Windows system component — leave managed by Windows",
    "Windows security component — leaving it enabled is recommended",
    "Security component — disabling can reduce active protection",
    "Hardware-sensitive utility — manages device or driver features",
})


def is_system_managed(reason: str) -> bool:
    """True when *reason* is one the classifier gives a system-owned entry."""
    return reason in _SYSTEM_MANAGED_REASONS


def _classify_impact(name: str, path: str, publisher: str, product_name: str) -> str:
    return _infer_role(name, path, publisher, product_name)


def _build_recommendation(risk: str, role: str) -> str:
    # Keyed on the role, not on a tier. The advice for a security component or
    # a driver utility is the same whatever bucket it is filed under, and it
    # used to be reachable only through the retired Protected tier — folding
    # that into Review would have silently replaced this with the generic
    # "review the publisher and path" line.
    if role == "Security component":
        return "Leaving it enabled is usually recommended for ongoing protection."
    if role == "Hardware utility":
        return "Keep it only if you rely on its hardware features after login."
    if risk == "Optional":
        if role == "Background sync":
            return "If you use it daily, keeping it enabled is more convenient."
        if role == "Remote access service":
            return "Keep it enabled only if you expect remote access right after login."
        return "Disabling only affects automatic launch at login."
    if risk == "Safe":
        return "Safe to disable if you do not need it ready immediately after sign-in."
    return "Review the publisher and path before changing this startup entry."


def _build_explanation(name: str, publisher: str, role: str, risk: str) -> str:
    product = name or "This startup entry"
    maker = publisher or "an unknown publisher"

    role_text = {
        "Security component": f"{product} from {maker} starts with Windows to keep security or secure connectivity active.",
        "Hardware utility": f"{product} from {maker} launches at sign-in to manage hardware controls, drivers, or vendor features.",
        "Background sync": f"{product} from {maker} launches at sign-in so files stay synchronized in the background.",
        "Remote access service": f"{product} from {maker} starts automatically so remote access is available after login.",
        "Communication app": f"{product} from {maker} starts at login so messages, calls, or meeting notifications are ready right away.",
        "Game launcher": f"{product} from {maker} starts with Windows to keep game services, updates, or quick launch features ready.",
        "Creative helper": f"{product} from {maker} launches at sign-in to keep companion features available for creative or writing workflows.",
        "Update helper": f"{product} from {maker} starts with Windows to check for updates and background maintenance tasks.",
        "Light utility": f"{product} from {maker} is a small convenience utility that opens at login for faster access.",
        "Startup item": f"{product} from {maker} is configured to launch automatically at login.",
    }.get(role, f"{product} from {maker} is configured to launch automatically at login.")

    effect_text = {
        "Security component": "Disabling it can reduce active protection or delay secure connections until you open it manually.",
        "Hardware utility": "Disabling startup usually only stops automatic launch, but some vendor controls or notifications may not appear until you open it yourself.",
        "Background sync": "Disabling startup pauses automatic sync until the app is opened manually.",
        "Remote access service": "Disabling startup means remote access will not be ready until the app is launched manually.",
        "Communication app": "Disabling startup only stops it from opening automatically and delays notifications until you launch it.",
        "Game launcher": "Disabling startup only prevents automatic launch at login; games can still be started manually.",
        "Creative helper": "Disabling startup removes background convenience features until the app is opened manually.",
        "Update helper": "Disabling startup mainly delays automatic update checks until the app runs manually.",
        "Light utility": "Disabling startup usually only removes the convenience of having it ready immediately after sign-in.",
        "Startup item": "Its exact purpose at startup is less clear, so review the path and publisher before changing it.",
    }.get(role, "Its exact purpose at startup is less clear, so review the path and publisher before changing it.")

    recommendation = _build_recommendation(risk, role)
    return f"{role_text} {effect_text} {recommendation}"


# ── Publisher lookup via file version info ────────────────────────

def _read_version_value(exe_path: str, field_name: str) -> str:
    if not exe_path or not os.path.isfile(exe_path):
        return ""
    try:
        size = ctypes.windll.version.GetFileVersionInfoSizeW(exe_path, None)
        if not size:
            return ""
        buf = ctypes.create_string_buffer(size)
        if not ctypes.windll.version.GetFileVersionInfoW(exe_path, 0, size, buf):
            return ""
        pvoid = ctypes.c_void_p()
        plen = ctypes.c_uint()
        for sub in (
            f"\\StringFileInfo\\040904B0\\{field_name}",
            f"\\StringFileInfo\\040904E4\\{field_name}",
            f"\\StringFileInfo\\000004B0\\{field_name}",
        ):
            if (ctypes.windll.version.VerQueryValueW(
                    buf, sub, ctypes.byref(pvoid), ctypes.byref(plen))
                    and plen.value > 1):
                return ctypes.wstring_at(pvoid, plen.value - 1).strip()
    except Exception:
        pass
    return ""


def _target_mtime(exe_path: str) -> float:
    """mtime of the startup target, 0.0 when it cannot be read.

    Cheap — one stat per entry, on a list that is tens of items long — and it
    is the only staleness signal available for a startup entry, which has no
    size or last-used data of its own.
    """
    if not exe_path:
        return 0.0
    try:
        return os.stat(exe_path).st_mtime
    except OSError:
        return 0.0


def _infer_publisher(exe_path: str) -> str:
    # Whole-word, for the same reason as _search_space: several of these tokens
    # are three or four letters ("amd", "gog", "epic"), and as substrings they
    # claimed a publisher for any path that happened to contain them —
    # "Diamond" answering to "amd", "Epicor" to "epic".
    space = _search_space(exe_path or "", Path(exe_path).stem if exe_path else "")
    for token, publisher in _KNOWN_PUBLISHERS.items():
        if _contains_any(space, (token,)):
            return publisher
    return ""


def _get_publisher(exe_path: str) -> str:
    """Resolve publisher from version metadata first, then known path inference."""
    publisher = _read_version_value(exe_path, "CompanyName")
    if publisher:
        return publisher
    product = _read_version_value(exe_path, "ProductName")
    if product:
        inferred = _infer_publisher(product)
        if inferred:
            return inferred
    return _infer_publisher(exe_path)


def _get_product_name(exe_path: str, fallback_name: str) -> str:
    product = _read_version_value(exe_path, "ProductName")
    if product:
        return product
    desc = _read_version_value(exe_path, "FileDescription")
    if desc:
        return desc
    return fallback_name


# ── Command → exe path extractor ─────────────────────────────────

def _extract_exe(command: str) -> str:
    """Extract and resolve the executable path from a command line."""
    if not command:
        return ""
    cmd = os.path.expandvars(command.strip())

    # Quoted path
    if cmd.startswith('"'):
        end = cmd.find('"', 1)
        if end > 0:
            return cmd[1:end]

    # Try progressively shorter prefix tokens
    parts = cmd.split()
    for i in range(min(len(parts), 4), 0, -1):
        candidate = " ".join(parts[:i])
        if os.path.isfile(candidate):
            return candidate

    return parts[0] if parts else ""


# ── Startup folder LNK resolution ────────────────────────────────

def _resolve_lnk_binary(lnk_path: str) -> str:
    """Parse MS-SHLLINK binary format to extract the local target path.

    Implements the minimal subset needed: header validation → optional IDList
    skip → LinkInfo LocalBasePath (ASCII) and LocalBasePathUnicode (UTF-16LE).
    All reads are bounds-checked; any exception returns ''.
    """
    try:
        with open(lnk_path, "rb") as f:
            data = f.read(8192)

        if len(data) < 0x4C:
            return ""

        # Validate ShellLinkHeader size (must be 0x4C)
        if struct.unpack_from("<I", data, 0)[0] != 0x4C:
            return ""

        link_flags = struct.unpack_from("<I", data, 0x14)[0]
        has_id_list  = bool(link_flags & 0x01)
        has_link_info = bool(link_flags & 0x02)

        pos = 0x4C  # skip 76-byte header

        # Skip optional LinkTargetIDList
        if has_id_list:
            if pos + 2 > len(data):
                return ""
            id_list_size = struct.unpack_from("<H", data, pos)[0]
            pos += 2 + id_list_size

        if not has_link_info or pos + 28 > len(data):
            return ""

        # LinkInfo header fields
        li_hsize = struct.unpack_from("<I", data, pos + 4)[0]
        li_flags = struct.unpack_from("<I", data, pos + 8)[0]
        lbp_off  = struct.unpack_from("<I", data, pos + 16)[0]  # LocalBasePathOffset

        if not (li_flags & 0x01):  # VolumeIDAndLocalBasePath not set
            return ""

        # Try Unicode LocalBasePath first (header must be >= 0x24 = 36 bytes)
        if li_hsize >= 0x24 and pos + 32 <= len(data):
            lbp_uni_off = struct.unpack_from("<I", data, pos + 28)[0]
            if lbp_uni_off:
                p = pos + lbp_uni_off
                # Scan for UTF-16LE null terminator (two consecutive zero bytes
                # aligned to 2-byte boundary)
                end = p
                while end + 1 < len(data) and not (data[end] == 0 and data[end + 1] == 0):
                    end += 2
                if end > p:
                    try:
                        path = data[p:end].decode("utf-16-le")
                        if path:
                            return path
                    except (UnicodeDecodeError, Exception):
                        pass

        # Fallback: ASCII LocalBasePath
        if lbp_off:
            p = pos + lbp_off
            end = data.find(b'\x00', p)
            if end > p:
                try:
                    return data[p:end].decode("mbcs", errors="replace")
                except Exception:
                    pass

        return ""
    except Exception:
        return ""


def _resolve_lnk(lnk_path: str) -> str:
    """Return the target path of a .lnk shortcut, or '' on failure.

    Tries win32com first (exact resolution including network paths).
    Falls back to binary MS-SHLLINK parsing if win32com is unavailable.
    If both fail, returns '' so the caller shows the .lnk path itself.
    """
    try:
        import win32com.client  # type: ignore
        shell = win32com.client.Dispatch("WScript.Shell")
        shortcut = shell.CreateShortcut(lnk_path)
        return shortcut.TargetPath or ""
    except Exception:
        pass
    return _resolve_lnk_binary(lnk_path)


# ── StartupApproved key reader ────────────────────────────────────

def _read_approved(hive, approved_path: str) -> dict[str, bool]:
    """Return {lower(name): is_enabled} from a StartupApproved key."""
    result: dict[str, bool] = {}
    try:
        import winreg
        with winreg.OpenKey(hive, approved_path) as key:
            idx = 0
            while True:
                try:
                    name, data, _ = winreg.EnumValue(key, idx)
                    idx += 1
                    if isinstance(data, bytes) and data:
                        # Byte 0: 0x02/0x06 = enabled, 0x03/0x01 = disabled
                        result[name.lower()] = data[0] in (0x00, 0x02, 0x06)
                    else:
                        result[name.lower()] = True  # no data → assume enabled
                except OSError:
                    break
    except OSError:
        pass
    return result


# ── Registry Run key reader ───────────────────────────────────────

def _read_run_key(hive, key_path: str, source: str, source_label: str,
                  approved: dict[str, bool]) -> list[StartupEntry]:
    """Read all values from a Run registry key and return StartupEntry objects."""
    entries: list[StartupEntry] = []
    try:
        import winreg
        with winreg.OpenKey(hive, key_path) as key:
            idx = 0
            while True:
                try:
                    name, command, _ = winreg.EnumValue(key, idx)
                    idx += 1
                    if not name or not command:
                        continue

                    # enabled/disabled from StartupApproved (default: enabled)
                    enabled = approved.get(name.lower(), True)
                    exe = _extract_exe(str(command))
                    publisher = _get_publisher(exe)
                    product_name = _get_product_name(exe, name)
                    impact = _classify_impact(name, exe, publisher, product_name)
                    risk, reason = _classify_risk(name, exe, publisher, product_name)

                    entries.append(StartupEntry(
                        name=name,
                        command=str(command),
                        path=exe,
                        publisher=publisher,
                        product_name=product_name,
                        source=source,
                        source_label=source_label,
                        enabled=enabled,
                        risk=risk,
                        system_managed=is_system_managed(reason),
                        risk_reason=reason,
                        impact=impact,
                        recommendation=_build_recommendation(risk, impact),
                        explanation_fallback=_build_explanation(name, publisher or "Unknown publisher", impact, risk),
                        target_modified=_target_mtime(exe),
                    ))
                except OSError:
                    break
    except OSError:
        pass
    return entries


# ── Startup folder reader ─────────────────────────────────────────

def _read_startup_folder(folder: str, source: str, source_label: str) -> list[StartupEntry]:
    """Read .lnk shortcuts from a startup folder."""
    entries: list[StartupEntry] = []
    try:
        p = Path(os.path.expandvars(folder))
        if not p.is_dir():
            return []
        for item in p.iterdir():
            if item.suffix.lower() == ".lnk":
                name = item.stem
                target = _resolve_lnk(str(item))
                exe = _extract_exe(target) if target else ""
                if not exe and target:
                    exe = target
                publisher = _get_publisher(exe) if exe else ""
                product_name = _get_product_name(exe, name) if exe else name
                impact = _classify_impact(name, exe, publisher, product_name)
                risk, reason = _classify_risk(name, exe, publisher, product_name)
                if not target:
                    reason = f"{reason} (shortcut target unresolved)"
                entries.append(StartupEntry(
                    name=name,
                    command=target or str(item),
                    path=exe,
                    publisher=publisher,
                    product_name=product_name,
                    source=source,
                    source_label=source_label,
                    enabled=True,  # items in startup folder are always enabled
                    risk=risk,
                    system_managed=is_system_managed(reason),
                    risk_reason=reason,
                    impact=impact,
                    recommendation=_build_recommendation(risk, impact),
                    explanation_fallback=_build_explanation(name, publisher or "Unknown publisher", impact, risk),
                    target_modified=_target_mtime(exe),
                ))
    except OSError:
        pass
    return entries


# ── Scheduled Task reader ─────────────────────────────────────────
#
# Tasks are queried via `schtasks /query /xml`, which works for standard
# users (the System32\Tasks folder itself is not user-readable) and emits
# locale-independent XML. Only tasks with a LogonTrigger or BootTrigger
# are startup-relevant. The Microsoft\Windows subtree (OS plumbing) is
# skipped — those are OS maintenance tasks, not user startup items.


def _task_tag(elem) -> str:
    """Local XML tag name, namespace prefix stripped."""
    return elem.tag.rsplit("}", 1)[-1]


def _task_child(parent, name: str):
    """First direct child element with the given local tag name."""
    if parent is None:
        return None
    for child in parent:
        if _task_tag(child) == name:
            return child
    return None


def _task_child_text(parent, name: str, default: str = "") -> str:
    child = _task_child(parent, name)
    if child is None or child.text is None:
        return default
    return child.text.strip()


def _extract_task_info(root) -> Optional[dict]:
    """Pull startup-relevant info from a parsed <Task> element.

    Returns {command, arguments, enabled, trigger, author} for tasks that
    run at logon or system startup, or None for everything else (time
    triggers, non-Exec actions, disabled-only triggers).
    """
    triggers = _task_child(root, "Triggers")
    if triggers is None:
        return None

    trigger_kind = None
    for trg in triggers:
        kind = _task_tag(trg)
        if kind not in ("LogonTrigger", "BootTrigger"):
            continue
        if _task_child_text(trg, "Enabled", "true").lower() == "false":
            continue
        if kind == "LogonTrigger":
            trigger_kind = "logon"
            break
        trigger_kind = "boot"
    if trigger_kind is None:
        return None

    # Executable action only — skip ComHandler / SendEmail / ShowMessage.
    command = arguments = ""
    actions = _task_child(root, "Actions")
    if actions is not None:
        for act in actions:
            if _task_tag(act) == "Exec":
                command = _task_child_text(act, "Command")
                arguments = _task_child_text(act, "Arguments")
                break
    if not command:
        return None

    settings = _task_child(root, "Settings")
    enabled = (_task_child_text(settings, "Enabled", "true").lower() != "false"
               if settings is not None else True)

    reginfo = _task_child(root, "RegistrationInfo")
    author = _task_child_text(reginfo, "Author") if reginfo is not None else ""

    return {"command": command, "arguments": arguments,
            "enabled": enabled, "trigger": trigger_kind, "author": author}


def _query_task_xml() -> str:
    """Return the raw XML dump from `schtasks /query /xml`, or '' on failure."""
    try:
        result = subprocess.run(
            ["schtasks", "/query", "/xml"],
            capture_output=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0 or not result.stdout:
        return ""
    raw = result.stdout
    encoding = "utf-16" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8"
    return raw.decode(encoding, errors="replace")


# Pairs each "<!-- \task\path -->" marker with the <Task>...</Task> that
# follows it (an optional <?xml?> declaration sits between the two).
_TASK_BLOCK_RE = re.compile(
    r"<!--\s*(.*?)\s*-->\s*(?:<\?xml.*?\?>)?\s*(<Task\b.*?</Task>)",
    re.DOTALL,
)


def _read_scheduled_tasks() -> list[StartupEntry]:
    """Detect startup entries registered as Windows Scheduled Tasks.

    Keeps only tasks triggered at logon or boot. The Microsoft\\Windows
    task subtree (OS plumbing) is skipped to avoid flooding the list.
    """
    text = _query_task_xml()
    if not text:
        return []

    entries: list[StartupEntry] = []
    for task_path, block in _TASK_BLOCK_RE.findall(text):
        low = task_path.lower().replace("/", "\\")
        if low.startswith("\\microsoft\\windows\\"):
            continue
        try:
            info = _extract_task_info(ET.fromstring(block))
        except Exception:
            continue
        if info is None:
            continue

        name = task_path.rsplit("\\", 1)[-1] or task_path or "Scheduled Task"
        exe = _extract_exe(info["command"])
        publisher = _get_publisher(exe) if exe else ""
        if not publisher and info["author"]:
            publisher = _infer_publisher(info["author"])
        product_name = _get_product_name(exe, name) if exe else name

        full_command = info["command"]
        if info["arguments"]:
            full_command = f'{full_command} {info["arguments"]}'

        impact = _classify_impact(name, exe, publisher, product_name)
        risk, reason = _classify_risk(name, exe, publisher, product_name)
        label = ("Scheduled task (logon)" if info["trigger"] == "logon"
                 else "Scheduled task (startup)")

        entries.append(StartupEntry(
            name=name,
            command=full_command,
            path=exe,
            publisher=publisher,
            product_name=product_name,
            source="scheduled_task",
            source_label=label,
            enabled=info["enabled"],
            risk=risk,
            system_managed=is_system_managed(reason),
            risk_reason=reason,
            impact=impact,
            recommendation=_build_recommendation(risk, impact),
            explanation_fallback=_build_explanation(
                name, publisher or "Unknown publisher", impact, risk),
            target_modified=_target_mtime(exe),
        ))
    return entries


# ── Public API ────────────────────────────────────────────────────

def detect_startup_entries() -> list[StartupEntry]:
    """Detect all Windows startup entries from registry and startup folders.

    Returns a deduplicated list, ordered: Review → Optional → Protected → Safe,
    with enabled entries before disabled entries within each group.
    """
    try:
        import winreg
    except ImportError:
        return []  # Not on Windows

    all_entries: list[StartupEntry] = []
    seen_keys: set[str] = set()

    # ── Registry sources ──────────────────────────────────────────
    _SOURCES = [
        (winreg.HKEY_CURRENT_USER,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run",
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run",
         "run_hkcu", "User startup registry"),

        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run",
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run",
         "run_hklm", "System startup registry"),

        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Run",
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run32",
         "run_hklm_wow", "Windows startup registry (32-bit app)"),
    ]

    for hive, run_path, approved_path, source, source_label in _SOURCES:
        approved = _read_approved(hive, approved_path)
        for entry in _read_run_key(hive, run_path, source, source_label, approved):
            if entry.key not in seen_keys:
                seen_keys.add(entry.key)
                all_entries.append(entry)

    # ── Startup folders ───────────────────────────────────────────
    _FOLDERS = [
        (r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup",
         "startup_folder", "User startup folder"),
        (r"%ALLUSERSPROFILE%\Microsoft\Windows\Start Menu\Programs\Startup",
         "startup_folder_common", "Shared startup folder"),
    ]

    for folder, source, source_label in _FOLDERS:
        for entry in _read_startup_folder(folder, source, source_label):
            if entry.key not in seen_keys:
                seen_keys.add(entry.key)
                all_entries.append(entry)

    # ── Scheduled tasks (logon / boot triggered) ──────────────────
    for entry in _read_scheduled_tasks():
        if entry.key not in seen_keys:
            seen_keys.add(entry.key)
            all_entries.append(entry)

    # ── Sort: canonical risk order (Safe → Protected), enabled before disabled ─
    from app.models.risk import risk_sort_index
    all_entries.sort(key=lambda e: (risk_sort_index(e.risk), not e.enabled, e.name.lower()))

    return all_entries
