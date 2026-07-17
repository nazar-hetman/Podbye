"""Shared risk/status helpers for user-facing cleanup decisions."""

from __future__ import annotations

RISK_SAFE = "Safe"
RISK_OPTIONAL = "Optional"
RISK_REVIEW = "Review"
RISK_PROTECTED = "Protected"

RISK_ORDER = (RISK_SAFE, RISK_OPTIONAL, RISK_REVIEW, RISK_PROTECTED)
LEGACY_RISK = "Risk"


def normalize_risk(value: str | None) -> str:
    """Return the canonical risk label used by the UI."""
    risk = (value or RISK_REVIEW).strip()
    if risk == LEGACY_RISK:
        return RISK_REVIEW
    if risk in RISK_ORDER:
        return risk
    return RISK_REVIEW


def is_protected(value: str | None) -> bool:
    return normalize_risk(value) == RISK_PROTECTED


def needs_cleanup_confirmation(value: str | None) -> bool:
    """True for items that should require explicit cleanup confirmation."""
    return normalize_risk(value) in {RISK_REVIEW, RISK_PROTECTED}


def normalized_risk_totals(totals: dict | None) -> dict[str, int]:
    counts = {risk: 0 for risk in RISK_ORDER}
    for raw, value in (totals or {}).items():
        try:
            amount = int(value or 0)
        except (TypeError, ValueError):
            amount = 0
        counts[normalize_risk(str(raw))] += amount
    return counts


# ── Canonical risk → colour / badge variant ──────────────────────
# Single source of truth for how every risk level is coloured. All screens
# (findings table, dashboard, finding detail, cleanup dialog) route through
# these so a level — especially "Optional" — never renders as four different
# colours across the app.

# risk → (soft-background palette key, foreground palette key)
_RISK_PALETTE_KEYS = {
    RISK_SAFE:      ("safe_soft",     "safe"),
    RISK_OPTIONAL:  ("optional_soft", "optional"),
    RISK_REVIEW:    ("review_soft",   "review"),
    RISK_PROTECTED: ("risk_soft",     "risk"),
}

# risk → Badge() variant name (see app/widgets/pills.py _VARIANT_MAP)
_RISK_BADGE_VARIANT = {
    RISK_SAFE:      "safe",
    RISK_OPTIONAL:  "optional",
    RISK_REVIEW:    "review",
    RISK_PROTECTED: "protected",
}


def risk_variant(value: str | None) -> str:
    """Badge variant name for a risk level (canonical)."""
    return _RISK_BADGE_VARIANT.get(normalize_risk(value), "info")


def risk_colors(value: str | None) -> tuple[str, str]:
    """Return (background_hex, foreground_hex) for a risk badge, theme-aware."""
    from app.themes.theme_manager import get_palette
    p = get_palette()
    bg_key, fg_key = _RISK_PALETTE_KEYS.get(normalize_risk(value), (None, "text_dim"))
    bg = p.get(bg_key, p.get("panel_alt", "#18241e")) if bg_key else p.get("panel_alt", "#18241e")
    fg = p.get(fg_key, p.get("text_dim", "#8a9b8f"))
    return bg, fg


def risk_fg(value: str | None) -> str:
    """Foreground/accent colour for a risk level, theme-aware."""
    return risk_colors(value)[1]


def reclaimable_bytes(value: str | None, size_bytes: int, *, age_boost: float = 0.0) -> int:
    """Bytes a scan should count as reclaimable for an item — one formula.

    Both scan modes (raw Finding and grouped SmartEntity) route through this so
    the reported "reclaimable" total means the same thing regardless of mode:
      • Safe                → fully reclaimable
      • Review + age_boost  → partial credit (stale data, e.g. 5y+ → 40%)
      • everything else     → 0 (needs a human decision first)

    Duplicate groups are a special case handled by the caller (only the extra
    copies are reclaimable), so they pass their own value rather than calling
    this with the group's full size.
    """
    risk = normalize_risk(value)
    if risk == RISK_SAFE:
        return int(size_bytes)
    if risk == RISK_REVIEW and age_boost > 0:
        return int(size_bytes * age_boost)
    return 0
