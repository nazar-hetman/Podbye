"""Single source of truth for the application version.

Imported by the Settings/About panel and the sidebar so the version never
drifts between them. Bump ``__version__`` (and ``BUILD`` when cutting a build)
for a new release.
"""
from __future__ import annotations

__version__ = "1.0.0-beta.3"
BUILD = "2026.08"


def short_version() -> str:
    """Compact form for the sidebar wordmark, e.g. 'v1.0.0-beta.2'."""
    return f"v{__version__}"
