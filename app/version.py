"""Single source of truth for the application version.

Imported by the Settings/About panel and the sidebar so the version never
drifts between them. Bump ``__version__`` (and ``BUILD`` when cutting a build)
for a new release.
"""
from __future__ import annotations

__version__ = "1.0.0-beta.5"
BUILD = "2026.09"

# Where the source and the releases live. Held here beside the version because
# the two are read together: About shows what you are running and where a newer
# one would be published.
#
# Podbye never fetches either of these. The About panel hands the URL to the
# system browser and that is the end of Podbye's involvement — an in-app update
# check would be an outbound request from a program whose entire promise is
# that it does not make any, and would announce the user's IP, version and
# launch time to a server on every start. See tests/test_offline_guarantee.py.
REPO_URL = "https://github.com/nazar-hetman/Podbye"
RELEASES_URL = f"{REPO_URL}/releases"

# The two local runtimes Podbye can talk to, for the one state where the user
# has neither and the app would otherwise say "install one" and stop helping.
# Same rule as above: these are handed to the system browser and never fetched.
# Official download pages only — a link Podbye offers has to be one the user
# would have found themselves, not a mirror or a direct binary.
OLLAMA_URL = "https://ollama.com/download"
LM_STUDIO_URL = "https://lmstudio.ai"


def short_version() -> str:
    """Compact form for the sidebar wordmark, e.g. 'v1.0.0-beta.2'."""
    return f"v{__version__}"
