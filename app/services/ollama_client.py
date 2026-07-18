"""Local AI client — Ollama, LM Studio and llama.cpp.

Handles connection testing, model discovery, and prompt generation for the
three popular local runtimes. LM Studio and llama.cpp both expose an
OpenAI-compatible HTTP API, so one extra request shape covers both.

The backend is detected from the endpoint rather than configured: the user
enters an address and it works. ``/api/tags`` identifies Ollama, ``/v1/models``
identifies an OpenAI-compatible server.

Local only, and enforced: every request refuses an endpoint that is not
loopback or LAN, so "server" means this machine or a mini-PC on your own
network — never a cloud API.

(The module name is historical; it is no longer Ollama-specific.)
"""
from __future__ import annotations

import json
import re
import socket
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
from urllib.parse import urlparse
from typing import Tuple


# The built-in "Local" endpoint — Ollama's default on this machine.
LOCAL_ENDPOINT = "http://127.0.0.1:11434"

# Backends. OPENAI covers LM Studio and llama.cpp (and anything else serving
# the same routes) — they differ in packaging, not in protocol.
BACKEND_OLLAMA = "ollama"
BACKEND_OPENAI = "openai"

BACKEND_LABELS = {
    BACKEND_OLLAMA: "Ollama",
    BACKEND_OPENAI: "LM Studio / llama.cpp",
}

# endpoint → backend, so a scan does not re-probe on every single request.
_BACKEND_CACHE: dict[str, str] = {}


def _get_json(url: str, timeout: int):
    """GET *url* and parse JSON, or return None."""
    try:
        with urlopen(Request(url, method="GET"), timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def detect_backend(endpoint: str, timeout: int = 4,
                   force_refresh: bool = False) -> str | None:
    """Identify which local runtime is serving *endpoint*.

    Probes Ollama's native route first, then the OpenAI-compatible one, so the
    user never has to declare which server they run. Returns None when nothing
    answers.
    """
    if not is_local_endpoint(endpoint):
        return None
    base = endpoint.rstrip("/")
    if not force_refresh and base in _BACKEND_CACHE:
        return _BACKEND_CACHE[base]

    backend = None
    if isinstance(_get_json(f"{base}/api/tags", timeout), dict):
        backend = BACKEND_OLLAMA
    elif isinstance(_get_json(f"{base}/v1/models", timeout), dict):
        backend = BACKEND_OPENAI

    if backend:
        _BACKEND_CACHE[base] = backend
    return backend


def reset_backend_cache() -> None:
    _BACKEND_CACHE.clear()

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

    backend = detect_backend(endpoint, timeout=timeout, force_refresh=True)
    if backend is None:
        return False, "offline · no Ollama, LM Studio or llama.cpp server found"
    count = len(list_models(endpoint, timeout=timeout))
    label = BACKEND_LABELS.get(backend, backend)
    return True, f"online · {label} · {count} model{'s' if count != 1 else ''} available"


def list_models(endpoint: str, timeout: int = 4) -> list:
    """Fetch available models from Ollama /api/tags.

    Returns list of dicts: [{"name": "model:tag", "size": bytes, ...}]
    """
    if not is_local_endpoint(endpoint):
        return []
    base = endpoint.rstrip("/")
    backend = detect_backend(endpoint, timeout=timeout)

    if backend == BACKEND_OLLAMA:
        data = _get_json(f"{base}/api/tags", timeout) or {}
        return [
            {
                "name": m.get("name", "unknown"),
                "size": m.get("size", 0),
                "modified": m.get("modified_at", ""),
            }
            for m in data.get("models", [])
        ]

    if backend == BACKEND_OPENAI:
        # OpenAI-compatible servers list models as {"data": [{"id": ...}]} and
        # report no size, so the UI simply shows no size for them.
        data = _get_json(f"{base}/v1/models", timeout) or {}
        return [
            {"name": m.get("id", "unknown"), "size": 0, "modified": ""}
            for m in data.get("data", [])
            if m.get("id")
        ]

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


def _extract_text(body: dict, backend: str) -> str:
    """Pull the generated text out of whichever response shape came back.

    Ollama returns {"response": ...}; OpenAI-compatible servers return
    {"choices": [{"message": {"content": ...}}]}. Some llama.cpp builds answer
    the completions shape ({"choices": [{"text": ...}]}) even on the chat
    route, so both are accepted.
    """
    if backend == BACKEND_OPENAI:
        choices = body.get("choices") or []
        if choices:
            first = choices[0] or {}
            msg = (first.get("message") or {}).get("content")
            return (msg or first.get("text") or "").strip()
        return ""
    return (body.get("response") or "").strip()


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

    base = endpoint.rstrip("/")
    backend = detect_backend(endpoint, timeout=min(timeout, 5)) or BACKEND_OLLAMA

    if backend == BACKEND_OPENAI:
        # LM Studio and llama.cpp: chat completions is the common denominator.
        url = f"{base}/v1/chat/completions"
        body_dict = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }
        # These servers take generation settings at the top level, not nested
        # under "options" the way Ollama does.
        body_dict.update(options or {})
    else:
        url = f"{base}/api/generate"
        body_dict = {"model": model, "prompt": prompt, "stream": False}
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
            text = strip_reasoning(_extract_text(body, backend))
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
