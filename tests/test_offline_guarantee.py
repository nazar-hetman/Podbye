"""Podbye is an offline tool, and that has to be enforced, not just claimed.

The product promise is that nothing about a user's disk ever leaves their
machine — no telemetry, no crash upload, no "anonymous usage statistics". That
is the reason to trust it over the other cleaners, and the reason the source
is public. A promise like that is worth exactly as much as the test that
protects it, so this file fails the build if outbound networking appears.

The single permitted exception is the local model runtime (Ollama / LM Studio),
which the user opts into and which is restricted to loopback or their own LAN.
"""
import ast
import pathlib

import pytest

APP = pathlib.Path(__file__).resolve().parents[1] / "app"

# Only this module may open a socket, and only under the LAN guard below.
NETWORK_MODULE = "services/ollama_client.py"

_NET_IMPORTS = {
    "requests", "httpx", "aiohttp", "urllib3", "socket", "http",
    "urllib.request", "ftplib", "smtplib", "telnetlib", "websocket",
    "websockets", "boto3", "google.cloud", "sentry_sdk", "posthog",
    "mixpanel", "analytics", "segment",
}


def _modules_imported_by(path: pathlib.Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def _app_files():
    return [p for p in APP.rglob("*.py") if "__pycache__" not in str(p)]


def test_only_the_ai_client_can_reach_the_network():
    offenders = []
    for path in _app_files():
        rel = path.relative_to(APP).as_posix()
        if rel == NETWORK_MODULE:
            continue
        for module in _modules_imported_by(path):
            root = module.split(".")[0]
            if module in _NET_IMPORTS or root in _NET_IMPORTS:
                offenders.append(f"{rel} imports {module}")
    assert not offenders, (
        "networking outside the AI client — Podbye promises to work entirely "
        "offline:\n  " + "\n  ".join(offenders))


def test_no_telemetry_or_analytics_sdk_is_bundled():
    """A dependency is enough to break the promise, used or not."""
    requirements = (APP.parent / "requirements.txt").read_text(encoding="utf-8").lower()
    for banned in ("sentry", "posthog", "mixpanel", "segment", "analytics",
                   "bugsnag", "rollbar", "datadog", "amplitude"):
        assert banned not in requirements, f"{banned} in requirements.txt"


@pytest.mark.parametrize("endpoint,expected", [
    ("http://localhost:11434", True),
    ("http://127.0.0.1:11434", True),
    ("http://192.168.1.50:11434", True),
    ("http://10.0.0.5:11434", True),
    ("http://172.16.4.2:11434", True),
    # Anything on the public internet must be refused, however plausible.
    ("https://api.openai.com/v1", False),
    ("https://api.anthropic.com", False),
    ("http://8.8.8.8:11434", False),
    ("https://example.com/ollama", False),
    ("", False),
])
def test_the_ai_endpoint_is_restricted_to_this_machine_or_the_lan(endpoint, expected):
    from app.services.ollama_client import is_local_endpoint
    assert is_local_endpoint(endpoint) is expected, endpoint


def test_a_hostname_resolving_off_lan_is_refused():
    """The guard resolves names, so a public host cannot sneak past by
    hiding behind a friendly-looking hostname."""
    from app.services.ollama_client import is_local_endpoint
    assert is_local_endpoint("http://one.one.one.one:11434") is False
