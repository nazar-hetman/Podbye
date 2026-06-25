"""StartupEntry model — a single Windows startup program entry."""
from __future__ import annotations

from dataclasses import dataclass, field


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
    risk: str                   # "Safe" | "Optional" | "Review" | "Protected"
    risk_reason: str
    impact: str                 # Human-readable startup role / effect
    product_name: str = ""
    recommendation: str = ""
    explanation_fallback: str = ""

    # AI fields
    ai_status: str = "none"     # "none" | "pending" | "analyzing" | "ready" | "failed" | "disabled"
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
