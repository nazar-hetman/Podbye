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


def strip_reasoning(text: str) -> str:
    """Remove <think>…</think> reasoning blocks some models emit before the answer.

    Reasoning models (qwen3, deepseek-r1, …) prepend their chain-of-thought in a
    <think> block. That is not the explanation — left in, it dumps a wall of
    "let me consider…" into the UI. Strip the block and keep only the answer.
    A cheap, model-agnostic guard: no effect on models that never emit one.
    """
    lowered = text.lower()
    if "<think>" in lowered:
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
        # An unterminated block (truncated mid-thought) — keep only the text
        # before the dangling open tag so raw chain-of-thought never surfaces.
        if "<think>" in text.lower():
            text = re.split(r"<think>", text, maxsplit=1, flags=re.IGNORECASE)[0]
    return text.strip()


def generate(
    endpoint: str,
    model: str,
    prompt: str,
    timeout: int = 180,
    cancel_flag=None,
    options: dict | None = None,
) -> Tuple[bool, str]:
    """Send a prompt to Ollama /api/generate (non-streaming).

    *timeout*: emergency safety timeout only. Normal operation waits for the
    model to finish. This only fires if the model completely stops responding.

    *cancel_flag*: an object with a boolean `is_set()` method (e.g. threading.Event).
    If set before or during the request, returns early.

    *options*: Ollama generation options (temperature, num_predict, …). A low
    temperature keeps factual explanations consistent; num_predict caps runaway
    output. Universally supported, so safe to always send.

    Returns (success, response_text_or_error).
    """
    if not is_local_endpoint(endpoint):
        return False, "refused · endpoint is not localhost or LAN"

    if cancel_flag and cancel_flag.is_set():
        return False, "cancelled"

    url = endpoint.rstrip("/") + "/api/generate"
    body_dict = {
        "model": model,
        "prompt": prompt,
        "stream": False,
    }
    if options:
        body_dict["options"] = options
    payload = json.dumps(body_dict).encode("utf-8")

    import time as _time
    t0 = _time.time()
    try:
        req = Request(url, data=payload, method="POST")
        req.add_header("Content-Type", "application/json")
        with urlopen(req, timeout=timeout) as resp:
            if cancel_flag and cancel_flag.is_set():
                return False, "cancelled"
            body = json.loads(resp.read().decode("utf-8"))
            text = strip_reasoning(body.get("response", "").strip())
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
