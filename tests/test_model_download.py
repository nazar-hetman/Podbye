"""Downloading a model through Ollama's HTTP API.

Every response here is fabricated. Nothing in this file requires Ollama to be
installed, running, or reachable, and no test touches the network: urlopen is
replaced for the duration of each test. That is deliberate - the suite already
learned twice this month what happens when a test quietly asks the host
machine a question.
"""
import io
import json

import pytest

from app.services import ollama_client as oc


LOCAL = "http://127.0.0.1:11434"

# Captured before the autouse fixture below replaces it, so the three tests
# that are *about* free space can still reach the real implementation.
_REAL_FREE_SPACE = oc.free_space_bytes


class _FakeStream(io.BytesIO):
    """A urlopen response: iterable by line, and a context manager."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _stream(*objects) -> _FakeStream:
    body = "".join(json.dumps(o) + "\n" for o in objects).encode("utf-8")
    return _FakeStream(body)


def _pull_lines(total=4_000_000_000):
    """The shape Ollama actually streams: manifest, layers, verify, success."""
    return [
        {"status": "pulling manifest"},
        {"status": "pulling sha256:aaa", "digest": "sha256:aaa",
         "total": total, "completed": 0},
        {"status": "pulling sha256:aaa", "digest": "sha256:aaa",
         "total": total, "completed": total // 2},
        {"status": "pulling sha256:aaa", "digest": "sha256:aaa",
         "total": total, "completed": total},
        {"status": "verifying sha256 digest"},
        {"status": "writing manifest"},
        {"status": "success"},
    ]


@pytest.fixture(autouse=True)
def plenty_of_disk(monkeypatch):
    """Space is not what these tests are about; the ones that are, override it."""
    monkeypatch.setattr(oc, "free_space_bytes", lambda path="": 500 * 1024 ** 3)


# ── model ids ─────────────────────────────────────────────────────

@pytest.mark.parametrize("model", [
    "gpt-oss:20b", "llama3.2:3b", "mistral", "qwen2.5-coder:7b",
    "hf.co/user/repo:Q4_K_M", "library/llama3:latest",
])
def test_a_real_model_id_is_accepted(model):
    assert oc.is_valid_model_id(model) is True


@pytest.mark.parametrize("bad", [
    "", "   ", "http://evil/model", "C:\\models\\thing.gguf",
    "two words", "x" * 250, ":justatag",
])
def test_input_that_cannot_be_a_model_is_rejected(bad):
    """Rejected before any request, so a typo never reaches the network."""
    assert oc.is_valid_model_id(bad) is False


def test_a_malformed_id_is_never_sent(monkeypatch):
    called = []
    monkeypatch.setattr(oc, "urlopen",
                        lambda *a, **k: called.append(1) or _stream())
    code, detail = oc.pull_model(LOCAL, "not a model")
    assert code == oc.PULL_BAD_ID
    assert called == [], "a malformed id must not open a connection"


# ── the happy path ────────────────────────────────────────────────

def test_a_streamed_pull_reports_success(monkeypatch):
    monkeypatch.setattr(oc, "urlopen", lambda *a, **k: _stream(*_pull_lines()))
    code, detail = oc.pull_model(LOCAL, "gpt-oss:20b")
    assert code == oc.PULL_OK
    assert detail == "gpt-oss:20b"


def test_progress_is_reported_as_it_streams(monkeypatch):
    monkeypatch.setattr(oc, "urlopen", lambda *a, **k: _stream(*_pull_lines()))
    seen = []
    oc.pull_model(LOCAL, "gpt-oss:20b",
                  on_progress=lambda s, c, t: seen.append((s, c, t)))
    assert len(seen) == 7, seen
    assert seen[0] == ("pulling manifest", 0, 0), "no size is known yet"
    # the middle of the download carries real byte counts
    assert seen[2] == ("pulling sha256:aaa", 2_000_000_000, 4_000_000_000)
    assert seen[-1][0] == "success"


def test_nothing_is_invented_before_the_server_says_a_size(monkeypatch):
    monkeypatch.setattr(oc, "urlopen", lambda *a, **k: _stream(*_pull_lines()))
    seen = []
    oc.pull_model(LOCAL, "gpt-oss:20b",
                  on_progress=lambda s, c, t: seen.append((c, t)))
    assert seen[0] == (0, 0)


# ── refusals and failures ─────────────────────────────────────────

def test_an_unknown_model_fails_with_the_server_s_reason(monkeypatch):
    monkeypatch.setattr(oc, "urlopen", lambda *a, **k: _stream(
        {"status": "pulling manifest"},
        {"error": "pull model manifest: file does not exist"}))
    code, detail = oc.pull_model(LOCAL, "nosuchmodel:1b")
    assert code == oc.PULL_FAILED
    assert "does not exist" in detail


def test_a_stream_that_stops_short_is_not_a_success(monkeypatch):
    """A half-pull must never look finished - the model list would refresh
    around a model that is not installed."""
    monkeypatch.setattr(oc, "urlopen", lambda *a, **k: _stream(
        {"status": "pulling manifest"},
        {"status": "pulling sha256:aaa", "total": 100, "completed": 40}))
    code, _ = oc.pull_model(LOCAL, "gpt-oss:20b")
    assert code == oc.PULL_FAILED


def test_the_service_stopping_mid_download_is_reported_as_offline(monkeypatch):
    class _Dies(_FakeStream):
        def __iter__(self):
            yield json.dumps({"status": "pulling manifest"}).encode()
            raise ConnectionResetError("service stopped")

    monkeypatch.setattr(oc, "urlopen", lambda *a, **k: _Dies(b""))
    code, _ = oc.pull_model(LOCAL, "gpt-oss:20b")
    assert code == oc.PULL_OFFLINE


def test_ollama_not_running_is_reported_as_offline(monkeypatch):
    from urllib.error import URLError

    def _refuse(*a, **k):
        raise URLError("connection refused")

    monkeypatch.setattr(oc, "urlopen", _refuse)
    code, _ = oc.pull_model(LOCAL, "gpt-oss:20b")
    assert code == oc.PULL_OFFLINE


def test_a_non_local_endpoint_is_refused_without_a_request(monkeypatch):
    """The privacy guarantee holds for downloads too."""
    called = []
    monkeypatch.setattr(oc, "urlopen",
                        lambda *a, **k: called.append(1) or _stream())
    code, _ = oc.pull_model("http://api.example.com", "gpt-oss:20b")
    assert code == oc.PULL_REFUSED
    assert called == []


# ── cancellation ──────────────────────────────────────────────────

def test_cancelling_stops_the_stream(monkeypatch):
    monkeypatch.setattr(oc, "urlopen", lambda *a, **k: _stream(*_pull_lines()))
    seen = []

    def cancel_after_two():
        return len(seen) >= 2

    code, _ = oc.pull_model(LOCAL, "gpt-oss:20b",
                            on_progress=lambda s, c, t: seen.append(s),
                            should_cancel=cancel_after_two)
    assert code == oc.PULL_CANCELLED
    assert len(seen) == 2, "it stopped reading rather than draining the stream"


def test_a_cancelled_pull_is_not_a_success(monkeypatch):
    monkeypatch.setattr(oc, "urlopen", lambda *a, **k: _stream(*_pull_lines()))
    code, _ = oc.pull_model(LOCAL, "gpt-oss:20b", should_cancel=lambda: True)
    assert code != oc.PULL_OK


# ── disk space ────────────────────────────────────────────────────

def test_a_download_larger_than_the_disk_stops_before_writing(monkeypatch):
    monkeypatch.setattr(oc, "urlopen", lambda *a, **k: _stream(*_pull_lines()))
    monkeypatch.setattr(oc, "free_space_bytes", lambda path="": 1024 ** 3)  # 1 GB
    code, detail = oc.pull_model(LOCAL, "gpt-oss:20b")     # stream says 4 GB
    assert code == oc.PULL_NO_SPACE
    assert detail == "4000000000/1073741824"


def test_unknown_free_space_does_not_block_the_download(monkeypatch):
    """0 means the check failed, and a failed check must not become a warning."""
    monkeypatch.setattr(oc, "urlopen", lambda *a, **k: _stream(*_pull_lines()))
    monkeypatch.setattr(oc, "free_space_bytes", lambda path="": 0)
    code, _ = oc.pull_model(LOCAL, "gpt-oss:20b")
    assert code == oc.PULL_OK


def test_free_space_reads_the_ollama_models_override(monkeypatch, tmp_path):
    monkeypatch.setenv("OLLAMA_MODELS", str(tmp_path))
    assert oc.models_dir() == str(tmp_path)
    assert _REAL_FREE_SPACE() > 0


def test_free_space_walks_up_to_a_directory_that_exists(monkeypatch, tmp_path):
    """The models folder does not exist until the first pull."""
    monkeypatch.setenv("OLLAMA_MODELS", str(tmp_path / "not" / "yet" / "there"))
    assert _REAL_FREE_SPACE() > 0


def test_free_space_is_zero_rather_than_an_exception(monkeypatch):
    monkeypatch.setattr(oc, "models_dir", lambda: "")
    assert _REAL_FREE_SPACE() == 0


# ── metadata ──────────────────────────────────────────────────────

def test_model_details_reports_only_what_ollama_returned(monkeypatch):
    monkeypatch.setattr(oc, "urlopen", lambda *a, **k: _FakeStream(json.dumps({
        "license": "Apache License 2.0",
        "capabilities": ["completion", "tools"],
        "details": {"parameter_size": "20.9B", "quantization_level": "MXFP4",
                    "family": "gptoss"},
    }).encode()))
    d = oc.model_details(LOCAL, "gpt-oss:20b")
    assert d["parameter_size"] == "20.9B"
    assert d["quantization"] == "MXFP4"
    assert d["license"] == "Apache License 2.0"
    assert d["capabilities"] == ["completion", "tools"]


def test_absent_metadata_fields_are_omitted_not_faked(monkeypatch):
    monkeypatch.setattr(oc, "urlopen",
                        lambda *a, **k: _FakeStream(json.dumps({"details": {}}).encode()))
    assert oc.model_details(LOCAL, "mistral") == {}


def test_model_details_on_an_unreachable_server_is_empty(monkeypatch):
    def _boom(*a, **k):
        raise OSError("nothing there")

    monkeypatch.setattr(oc, "urlopen", _boom)
    assert oc.model_details(LOCAL, "mistral") == {}


def test_model_details_refuses_a_remote_endpoint():
    assert oc.model_details("http://api.example.com", "mistral") == {}
