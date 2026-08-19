"""Prompt builder for local AI explanations.

Generates tone- and length-aware prompts for Ollama-compatible models.
Each prompt describes a single scan finding and asks the model to explain
what it is, why it was flagged, and whether it is safe to remove.
"""
from __future__ import annotations


# ── Tone preambles ──────────────────────────────────────────────

_TONE_PREAMBLE = {
    "neutral": "You are a storage analysis assistant. Be concise and practical.",
    "friendly": "You are a helpful cleanup advisor. Be direct and practical.",
    "professional": "You are a systems consultant. Be concise and actionable.",
    "technical": "You are a systems engineer. Be precise about impacts.",
}

# ── Format rules ───────────────────────────────────────────────
#
# CRITICAL: All three lengths must produce PLAIN PROSE only.
# The LLM mirrors the structure of the instructions, so these rules
# use prose examples instead of numbered lists to prevent list output.

_FORMAT_RULES = {
    "compact": (
        "Write ONE sentence: what it is, then whether it is safe to remove. "
        "Example: 'TeamViewer is a remote desktop app — safe to remove if unused.' "
        "No bullets, no numbers, no markdown."
    ),
    "standard": (
        "Write exactly 3 sentences as plain prose in this order: "
        "(1) what it is and its purpose, "
        "(2) what breaks or is lost if it is removed, "
        "(3) a direct recommendation — keep, remove, or review. "
        "Example: 'Chrome User Data stores your browser profile including bookmarks, saved passwords and extensions. "
        "Removing it deletes all Chrome personalisation but does not break Windows. "
        "Safe to remove if you no longer use Chrome.' "
        "If you do not recognise the item, start with: 'Purpose unknown — limited information available.' "
        "No bullets, no numbers, no markdown."
    ),
    "detailed": (
        "Write exactly 4 sentences as plain prose in this order: "
        "(1) what it is — name and specific purpose, "
        "(2) what it stores or does on this system, "
        "(3) what breaks if removed — be specific, not generic, "
        "(4) a clear recommendation: keep, safe to remove, or review first. "
        "Example: 'TeamViewer is a remote desktop application for PC access and support. "
        "It stores connection profiles, session logs and configuration files. "
        "Removing it will uninstall TeamViewer but will not affect Windows stability. "
        "Safe to remove if you do not use remote access.' "
        "If you do not recognise the item, start with: 'Purpose unknown — limited information available.' "
        "No bullets, no numbers, no headings, no markdown."
    ),
}

# ── Directness rule ────────────────────────────────────────────

_DIRECTNESS = (
    "Say what kind of tool or data this is and what people use it for, in plain "
    "words a non-expert understands. If you are not sure of the exact product, "
    "name the closest well-known equivalent — for example 'an AI code-completion "
    "tool like GitHub Copilot'. Never define an item by repeating its own name "
    "('X contains files for X'), and do not use vague filler like 'contains files' "
    "or 'stores data'. State concrete impacts."
)


# Written in the target language on purpose. A small local model follows
# "Antworte ausschließlich auf Deutsch." far more reliably than the same
# instruction phrased in English — the sentence itself is evidence of which
# language is wanted. Every language the picker offers needs an entry here;
# tests/test_ai_languages.py fails the build if one is missing.
_LANGUAGE_INSTRUCTIONS = {
    "english": "Answer only in English.",
    "ukrainian": "Відповідай лише українською мовою.",
    "spanish": "Responde únicamente en español.",
    "german": "Antworte ausschließlich auf Deutsch.",
    "french": "Réponds uniquement en français.",
}


def _language_instruction(language: str) -> str:
    """Build strict language constraint for the prompt."""
    key = (language or "English").lower().strip()
    return _LANGUAGE_INSTRUCTIONS.get(key, f"Return explanation in {language} only. Do not use any other language.")


def _truncate_path(path: str, max_len: int = 120) -> str:
    """Shorten very long paths to reduce prompt token count."""
    if len(path) <= max_len:
        return path
    parts = path.replace("\\", "/").split("/")
    if len(parts) <= 4:
        return path[:max_len]
    return "/".join(parts[:2]) + "/.../" + "/".join(parts[-3:])


def build_prompt(
    *,
    path: str,
    name: str,
    is_dir: bool,
    size: str,
    category: str,
    risk: str,
    source_rule: str,
    modified: str,
    accessed: str,
    tone: str = "neutral",
    length: str = "standard",
    language: str = "English",
) -> str:
    """Build a complete prompt for one finding.

    Parameters mirror the Finding model fields.
    *tone*, *length*, and *language* come from user settings.
    """
    tone_key = tone.lower().strip()
    length_key = length.lower().strip()

    preamble = _TONE_PREAMBLE.get(tone_key, _TONE_PREAMBLE["neutral"])
    fmt = _FORMAT_RULES.get(length_key, _FORMAT_RULES["standard"])
    lang = _language_instruction(language)

    item_type = "folder" if is_dir else "file"
    display_path = _truncate_path(path)

    # Compact
    if length_key == "compact":
        return (
            f"{preamble} {fmt} {_DIRECTNESS} {lang}\n\n"
            f"{item_type.capitalize()}: {name}\n"
            f"Path: {display_path}\n"
            f"Size: {size}\n"
        )

    # Standard
    if length_key == "standard":
        return (
            f"{preamble} {fmt} {_DIRECTNESS} {lang}\n\n"
            f"{item_type.capitalize()}: {name}\n"
            f"Path: {display_path}\n"
            f"Size: {size} | Category: {category}\n"
        )

    # Detailed
    return (
        f"{preamble} {fmt} {_DIRECTNESS} {lang}\n\n"
        f"{item_type.capitalize()}: {name}\n"
        f"Path: {display_path}\n"
        f"Size: {size} | Category: {category}\n"
    )


# ── Entity-level format (Smart mode) ─────────────────────────────
#
# Same prose-only rules as _FORMAT_RULES to ensure consistent output.

_ENTITY_FORMAT = {
    "compact": (
        "Write ONE sentence: what it is, then whether it is safe to remove. "
        "Example: 'npm Packages for MyProject — safe to remove if the project is no longer active.' "
        "No bullets, no numbers, no markdown."
    ),
    "standard": (
        "Write exactly 3 sentences as plain prose in this order: "
        "(1) what this entity is and its purpose, "
        "(2) what breaks or is lost if it is removed, "
        "(3) a direct recommendation — keep, remove, or review. "
        "Example: 'Adobe Photoshop stores user presets, workspaces and scratch disk files. "
        "Removing it will delete your custom Photoshop settings but will not affect Windows. "
        "Safe to remove if you have uninstalled Photoshop.' "
        "If you do not recognise the item, start with: 'Purpose unknown — limited information available.' "
        "No bullets, no numbers, no markdown."
    ),
    "detailed": (
        "Write exactly 4 sentences as plain prose in this order: "
        "(1) what it is — name and specific purpose, "
        "(2) what it stores or does on this system, "
        "(3) what breaks if removed — be specific, not generic, "
        "(4) a clear recommendation: keep, safe to remove, or review first. "
        "Example: 'Discord stores the application, user settings and cached media for the gaming chat platform. "
        "It contains the main executable, local database and downloaded assets. "
        "Removing it will uninstall Discord but will not affect Windows or other applications. "
        "Safe to remove if you do not use Discord.' "
        "If you do not recognise the item, start with: 'Purpose unknown — limited information available.' "
        "No bullets, no numbers, no headings, no markdown."
    ),
}


def build_entity_prompt(
    *,
    path: str,
    name: str,
    entity_type: str,
    entity_type_label: str,
    size: str,
    file_count: int,
    folder_count: int,
    category: str,
    risk: str,
    children_sample: list[str],
    parent_app: str = "",
    is_internal: bool = False,
    app_version: str = "",
    app_publisher: str = "",
    tone: str = "neutral",
    length: str = "standard",
    language: str = "English",
) -> str:
    """Build a prompt for a SmartEntity (grouped folder/content).

    Unlike build_prompt(), this reasons about a grouped entity rather than
    a single file. The AI should identify the application/content and give
    a high-level recommendation.
    """
    tone_key = tone.lower().strip()
    length_key = length.lower().strip()

    preamble = _TONE_PREAMBLE.get(tone_key, _TONE_PREAMBLE["neutral"])
    fmt = _ENTITY_FORMAT.get(length_key, _ENTITY_FORMAT["standard"])
    lang = _language_instruction(language)
    display_path = _truncate_path(path)

    sample_str = ""
    if children_sample:
        sample_str = "Contents: " + ", ".join(children_sample[:8]) + "\n"
    
    # Build ownership context
    ownership_str = ""
    if parent_app:
        ownership_str = f"Part of: {parent_app}"
        if app_version:
            ownership_str += f" v{app_version}"
        if app_publisher:
            ownership_str += f" by {app_publisher}"
        ownership_str += "\n"
    elif is_internal:
        ownership_str = "Internal component of larger application\n"

    if length_key == "compact":
        return (
            f"{preamble} {fmt} {_DIRECTNESS} {lang}\n\n"
            f"Entity: {name}\n"
            f"Path: {display_path}\n"
            f"Type: {entity_type_label} | Size: {size} | Files: {file_count}\n"
            f"{ownership_str}"
        )

    if length_key == "standard":
        return (
            f"{preamble} {fmt} {_DIRECTNESS} {lang}\n\n"
            f"Entity: {name}\n"
            f"Path: {display_path}\n"
            f"Type: {entity_type_label} | Size: {size} | {file_count} files, {folder_count} folders\n"
            f"{ownership_str}"
            f"{sample_str}"
        )

    # Detailed
    return (
        f"{preamble} {fmt} {_DIRECTNESS} {lang}\n\n"
        f"Entity: {name}\n"
        f"Path: {display_path}\n"
        f"Type: {entity_type_label} | Size: {size} | Files: {file_count} | Folders: {folder_count}\n"
        f"{ownership_str}"
        f"{sample_str}"
    )
