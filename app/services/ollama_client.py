"""Ollama-compatible local AI client.

Handles connection testing, model discovery, and prompt generation.
No cloud. No external APIs. Local only.
"""
from __future__ import annotations

import json
import re
import socket
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
from urllib.parse import urlparse
from typing import Tuple


# Fallback model list if Ollama is offline
_FALLBACK_MODELS = [
    "llama3.2:3b",
    "qwen2.5:7b",
    "mistral",
    "gemma2:2b",
]

# Private / loopback / LAN ranges
_LOCAL_PATTERNS = re.compile(
    r"^(localhost|127\.\d+\.\d+\.\d+|10\.\d+\.\d+\.\d+|"
    r"172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|192\.168\.\d+\.\d+|"
    r"\[?::1\]?|0\.0\.0\.0)$",
    re.IGNORECASE,
)


def is_local_endpoint(endpoint: str) -> bool:
    """Return True if the endpoint host is loopback or LAN."""
    try:
        parsed = urlparse(endpoint)
        host = (parsed.hostname or "").strip("[]")
        if not host:
            return False
        if _LOCAL_PATTERNS.match(host):
            return True
        # Resolve hostname — accept if it resolves to a private IP
        try:
            addr = socket.gethostbyname(host)
            return _LOCAL_PATTERNS.match(addr) is not None
        except socket.gaierror:
            return False
    except Exception:
        return False


def test_connection(endpoint: str, timeout: int = 4) -> Tuple[bool, str]:
    """Test connection to an Ollama-compatible endpoint.

    Returns (success, message).
    - On success: (True, "online · N models available")
    - On failure: (False, "offline · reason")
    """
    if not is_local_endpoint(endpoint):
        return False, "refused · endpoint is not localhost or LAN"

    url = endpoint.rstrip("/") + "/api/tags"
    try:
        req = Request(url, method="GET")
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            models = data.get("models", [])
            count = len(models)
            return True, f"online · {count} model{'s' if count != 1 else ''} available"
    except HTTPError as e:
        return False, f"offline · HTTP {e.code}"
    except URLError as e:
        reason = str(e.reason) if hasattr(e, 'reason') else str(e)
        return False, f"offline · {reason}"
    except OSError as e:
        return False, f"offline · {e}"
    except Exception as e:
        return False, f"offline · {e}"


def list_models(endpoint: str, timeout: int = 4) -> list:
    """Fetch available models from Ollama /api/tags.

    Returns list of dicts: [{"name": "model:tag", "size": bytes, ...}]
    """
    if not is_local_endpoint(endpoint):
        return []

    url = endpoint.rstrip("/") + "/api/tags"
    try:
        req = Request(url, method="GET")
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            models = data.get("models", [])
            return [
                {
                    "name": m.get("name", "unknown"),
                    "size": m.get("size", 0),
                    "modified": m.get("modified_at", ""),
                }
                for m in models
            ]
    except Exception:
        return []


def generate(
    endpoint: str,
    model: str,
    prompt: str,
    timeout: int = 180,
    cancel_flag=None,
) -> Tuple[bool, str]:
    """Send a prompt to Ollama /api/generate (non-streaming).

    *timeout*: emergency safety timeout only. Normal operation waits for the
    model to finish. This only fires if the model completely stops responding.

    *cancel_flag*: an object with a boolean `is_set()` method (e.g. threading.Event).
    If set before or during the request, returns early.

    Returns (success, response_text_or_error).
    """
    if not is_local_endpoint(endpoint):
        return False, "refused · endpoint is not localhost or LAN"

    if cancel_flag and cancel_flag.is_set():
        return False, "cancelled"

    url = endpoint.rstrip("/") + "/api/generate"
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
    }).encode("utf-8")

    import time as _time
    t0 = _time.time()
    try:
        req = Request(url, data=payload, method="POST")
        req.add_header("Content-Type", "application/json")
        with urlopen(req, timeout=timeout) as resp:
            if cancel_flag and cancel_flag.is_set():
                return False, "cancelled"
            body = json.loads(resp.read().decode("utf-8"))
            text = body.get("response", "").strip()
            if not text:
                return False, "empty response from model"
            return True, text
    except HTTPError as e:
        if e.code == 404:
            return False, f"model not found: {model}"
        return False, f"HTTP {e.code}"
    except URLError as e:
        reason = str(e.reason) if hasattr(e, 'reason') else str(e)
        return False, f"server offline · {reason}"
    except socket.timeout:
        elapsed = _time.time() - t0
        return False, f"emergency timeout after {elapsed:.1f}s (limit {timeout}s)"
    except OSError as e:
        return False, f"connection error · {e}"
    except Exception as e:
        return False, f"error · {e}"


def fallback_models() -> list:
    """Return fallback model names for offline use."""
    return list(_FALLBACK_MODELS)


def format_model_size(size_bytes: int) -> str:
    """Format model size for display."""
    if size_bytes >= 1024 ** 3:
        return f"{size_bytes / (1024 ** 3):.1f} GB"
    if size_bytes >= 1024 ** 2:
        return f"{size_bytes / (1024 ** 2):.0f} MB"
    return f"{size_bytes / 1024:.0f} KB" if size_bytes > 0 else ""
