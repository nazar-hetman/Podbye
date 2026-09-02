"""StartupEntry model — a single Windows startup program entry."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StartupEntry:
    """A single Windows startup program entry."""

    # Identity
    name: str                   # Registry value name / shortcut name
    command: str                # Full command line as stored
    path: str                   # Resolved executable path (may be empty)
    publisher: str              # Company name from file version info or inference

    # Source
    source: str                 # "run_hkcu" | "run_hklm" | "run_hklm_wow" | "startup_folder" | "startup_folder_common"
    source_label: str           # "Registry (User)" etc.

    # Status
    enabled: bool               # Currently enabled?

    # Risk & impact
    risk: str                   # "Safe" | "Optional" | "Review"
    risk_reason: str
    impact: str                 # Human-readable startup role / effect
    product_name: str = ""
    recommendation: str = ""
    explanation_fallback: str = ""

    # mtime of the resolved executable, 0.0 when it could not be read (missing
    # target, permission denied). A startup entry pointing at a binary nothing
    # has touched in years is usually a leftover from an app the user stopped
    # using — the one triage signal the list had no way to show.
    target_modified: float = 0.0

    # Windows or a device vendor owns this one: a signed component in
    # System32, a security product, a driver utility. It is still Review —
    # Podbye changes no startup entry, so it can only advise — but the advice
    # is already exact, and the deterministic reason says more than a small
    # model would. Set by the classifier so the AI pass can skip it without
    # pattern-matching on reason text.
    system_managed: bool = False

    # AI fields
    # "unconfigured" is not a failure: no model is chosen, so nothing was
    # asked. Kept apart from "disabled" (startup AI switched off) and from
    # "failed" (asked, and it went wrong) because the three need different
    # answers from the user.
    ai_status: str = "none"     # none | pending | analyzing | ready | failed | disabled | unconfigured
    ai_explanation: str = ""
    ai_error: str = ""

    @property
    def key(self) -> str:
        """Stable dedup key."""
        return f"{self.source}|{self.name.lower()}"

    @property
    def status_label(self) -> str:
        return "Enabled" if self.enabled else "Disabled"

    @property
    def publisher_display(self) -> str:
        return self.publisher or "Unknown publisher"

    @property
    def target_modified_display(self) -> str:
        """``YYYY-MM-DD`` for the target binary, or "" when unknown."""
        if not self.target_modified:
            return ""
        from datetime import datetime
        try:
            return datetime.fromtimestamp(self.target_modified).strftime("%Y-%m-%d")
        except (OSError, ValueError, OverflowError):
            return ""
