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
