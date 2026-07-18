"""Local AI backends: Ollama, LM Studio and llama.cpp.

LM Studio and llama.cpp both speak the OpenAI-compatible API, so one request
shape covers both. The backend is detected from the endpoint — the user enters
an address and it works, without declaring which server they run.
"""
import json

import pytest

from app.services import ollama_client as oc
from app.services.ollama_client import (
    BACKEND_OLLAMA, BACKEND_OPENAI, detect_backend, generate, list_models,
)
# NOTE: ollama_client.test_connection is reached through the module, never
# imported by name — pytest would collect a bare "test_connection" as a test.

EP = "http://127.0.0.1:1234"


@pytest.fixture(autouse=True)
def _clear_cache():
    oc.reset_backend_cache()
    yield
    oc.reset_backend_cache()


@pytest.fixture
def server(monkeypatch):
    """A fake local server. ``routes`` maps URL suffix → JSON payload."""
    state = {"routes": {}, "posts": []}

    def fake_get(url, timeout):
        for suffix, payload in state["routes"].items():
            if url.endswith(suffix):
                return payload
        return None

    class _Resp:
        def __init__(self, payload):
            self._payload = payload
        def read(self):
            return json.dumps(self._payload).encode()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        url = req.full_url
        if req.data:
            state["posts"].append((url, json.loads(req.data.decode())))
        for suffix, payload in state["routes"].items():
            if url.endswith(suffix):
                return _Resp(payload)
        raise OSError("no such route")

    monkeypatch.setattr(oc, "_get_json", fake_get)
    monkeypatch.setattr(oc, "urlopen", fake_urlopen)
    return state


def _as_ollama(server):
    server["routes"]["/api/tags"] = {"models": [
        {"name": "gemma:2b", "size": 1700000000, "modified_at": "x"}]}
    server["routes"]["/api/generate"] = {"response": "ollama says hi"}


def _as_openai(server):
    server["routes"]["/v1/models"] = {"data": [{"id": "qwen2.5-7b-instruct"}]}
    server["routes"]["/v1/chat/completions"] = {
        "choices": [{"message": {"content": "lm studio says hi"}}]}


# ── detection ────────────────────────────────────────────────────


def test_detects_ollama(server):
    _as_ollama(server)
    assert detect_backend(EP) == BACKEND_OLLAMA


def test_detects_openai_compatible(server):
    _as_openai(server)
    assert detect_backend(EP) == BACKEND_OPENAI


def test_ollama_wins_when_both_answer(server):
    """Ollama's native route is probed first so it keeps its richer API."""
    _as_ollama(server)
    _as_openai(server)
    assert detect_backend(EP) == BACKEND_OLLAMA


def test_nothing_listening_is_none(server):
    assert detect_backend(EP) is None


def test_remote_endpoints_are_refused():
    """The LAN-only guarantee: a cloud address is never probed."""
    assert detect_backend("https://api.openai.com") is None


def test_detection_is_cached(server, monkeypatch):
    _as_openai(server)
    assert detect_backend(EP) == BACKEND_OPENAI
    calls = []
    monkeypatch.setattr(oc, "_get_json",
                        lambda u, t: calls.append(u) or None)
    assert detect_backend(EP) == BACKEND_OPENAI
    assert not calls, "cached endpoint was re-probed"


# ── model listing ────────────────────────────────────────────────


def test_lists_ollama_models(server):
    _as_ollama(server)
    models = list_models(EP)
    assert [m["name"] for m in models] == ["gemma:2b"]
    assert models[0]["size"] > 0


def test_lists_openai_models(server):
    _as_openai(server)
    models = list_models(EP)
    assert [m["name"] for m in models] == ["qwen2.5-7b-instruct"]
    assert models[0]["size"] == 0, "these servers report no size"


# ── generation ───────────────────────────────────────────────────


def test_generates_via_ollama(server):
    _as_ollama(server)
    ok, text = generate(EP, "gemma:2b", "hello")
    assert ok and text == "ollama says hi"
    url, body = server["posts"][-1]
    assert url.endswith("/api/generate")
    assert body["prompt"] == "hello"


def test_generates_via_openai_compatible(server):
    _as_openai(server)
    ok, text = generate(EP, "qwen2.5-7b-instruct", "hello")
    assert ok and text == "lm studio says hi"
    url, body = server["posts"][-1]
    assert url.endswith("/v1/chat/completions")
    assert body["messages"] == [{"role": "user", "content": "hello"}]


def test_options_go_where_each_backend_expects_them(server):
    """Ollama nests generation settings under "options"; OpenAI-compatible
    servers take them at the top level."""
    _as_ollama(server)
    generate(EP, "m", "p", options={"temperature": 0.2})
    assert server["posts"][-1][1]["options"] == {"temperature": 0.2}

    oc.reset_backend_cache()
    server["routes"].clear()
    _as_openai(server)
    generate(EP, "m", "p", options={"temperature": 0.2})
    body = server["posts"][-1][1]
    assert body["temperature"] == 0.2
    assert "options" not in body


def test_llama_cpp_completions_shape_is_accepted(server):
    """Some llama.cpp builds answer with {"text": ...} on the chat route."""
    server["routes"]["/v1/models"] = {"data": [{"id": "local"}]}
    server["routes"]["/v1/chat/completions"] = {"choices": [{"text": "raw shape"}]}
    ok, text = generate(EP, "local", "p")
    assert ok and text == "raw shape"


def test_reasoning_is_stripped_on_both_backends(server):
    _as_openai(server)
    server["routes"]["/v1/chat/completions"] = {
        "choices": [{"message": {"content": "<think>hmm</think>Answer."}}]}
    ok, text = generate(EP, "m", "p")
    assert ok and text == "Answer."


def test_empty_reply_is_an_error_not_a_blank_explanation(server):
    _as_openai(server)
    server["routes"]["/v1/chat/completions"] = {"choices": []}
    ok, msg = generate(EP, "m", "p")
    assert not ok


def test_generate_refuses_remote_endpoints():
    ok, msg = generate("https://api.openai.com", "gpt-4", "hi")
    assert not ok
    assert "refused" in msg.lower()


# ── connection status ────────────────────────────────────────────


def test_status_names_the_backend(server):
    _as_openai(server)
    ok, msg = oc.test_connection(EP)
    assert ok
    assert "LM Studio" in msg or "llama.cpp" in msg
    assert "1 model" in msg


def test_status_when_nothing_is_running(server):
    ok, msg = oc.test_connection(EP)
    assert not ok
    assert "LM Studio" in msg and "Ollama" in msg
