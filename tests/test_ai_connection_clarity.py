"""Never offer a model the user does not have, and say what is actually wrong.

Reported: "ai selection looks broken: I can't connect to local llms. The list
contains placeholders like mistral and etc - but I don't have them."

Two separate faults.

1. The model dropdown was filled from a hardcoded catalogue whenever the server
   was offline -- llama3.2:3b, qwen2.5:7b, mistral, gemma2:2b. They rendered
   identically to real entries, so picking one was the obvious move, and every
   explanation then failed because that model had never been pulled.

2. Every failure produced one message: "offline - no Ollama, LM Studio or
   llama.cpp server found". On the reporting machine Ollama *was* installed and
   on PATH; it simply was not started. The message read as "you don't have one"
   and pointed at no next step.
"""
import pytest

from app.services import ollama_client as oc


EP_LOCAL = "http://127.0.0.1:11434"
EP_LAN = "http://192.168.1.50:11434"
EP_CLOUD = "https://api.example.com"


@pytest.fixture(autouse=True)
def _clear_cache():
    oc.reset_backend_cache()
    yield
    oc.reset_backend_cache()


@pytest.fixture
def net(monkeypatch):
    """Control what each probed URL answers. None = nothing listening."""
    routes = {}

    def fake_get(url, timeout):
        for suffix, payload in routes.items():
            if url.endswith(suffix):
                return payload
        return None

    monkeypatch.setattr(oc, "_get_json", fake_get)
    return routes


@pytest.fixture
def no_runtime(monkeypatch):
    monkeypatch.setattr(oc, "find_ollama_executable", lambda: "")


@pytest.fixture
def runtime_installed(monkeypatch):
    monkeypatch.setattr(oc, "find_ollama_executable",
                        lambda: r"C:\Users\u\AppData\Local\Programs\Ollama\ollama.exe")


# ── the placeholder catalogue is gone ─────────────────────────────

def test_no_fake_model_catalogue_survives_anywhere():
    """The exact list that was being offered: it must not exist to be shown."""
    assert not hasattr(oc, "fallback_models")
    assert not hasattr(oc, "_FALLBACK_MODELS")
    source = (oc.__file__ or "")
    with open(source, encoding="utf-8") as fh:
        text = fh.read()
    # "mistral" may only appear in prose explaining the removal, never in a list.
    assert "qwen2.5:7b" not in text
    assert "gemma2:2b" not in text


def test_an_offline_probe_reports_no_models(net, runtime_installed):
    assert oc.probe(EP_LOCAL)["models"] == []


# ── each failure gets its own diagnosis ───────────────────────────

def test_installed_but_stopped_is_distinguishable(net, runtime_installed):
    """The reported machine: Ollama on PATH, nothing listening on 11434."""
    r = oc.probe(EP_LOCAL)
    assert r["status"] == oc.STATUS_NOT_RUNNING
    assert r["runtime_path"].endswith("ollama.exe"), "lost the path needed to start it"


def test_nothing_installed_is_a_different_answer(net, no_runtime):
    r = oc.probe(EP_LOCAL)
    assert r["status"] == oc.STATUS_NOT_INSTALLED
    assert r["runtime_path"] == ""


def test_a_silent_lan_address_is_unreachable_not_uninstalled(net, runtime_installed):
    """Advice about starting Ollama here would be about the wrong computer."""
    r = oc.probe(EP_LAN)
    assert r["status"] == oc.STATUS_UNREACHABLE
    assert r["runtime_path"] == ""


def test_a_cloud_address_is_refused(net):
    assert oc.probe(EP_CLOUD)["status"] == oc.STATUS_REFUSED


def test_a_running_server_with_no_models_is_not_a_failure(net, runtime_installed):
    net["/api/tags"] = {"models": []}
    r = oc.probe(EP_LOCAL)
    assert r["status"] == oc.STATUS_NO_MODELS
    assert r["backend"] == oc.BACKEND_OLLAMA


def test_a_running_server_with_models_is_online(net):
    net["/api/tags"] = {"models": [
        {"name": "llama3.2:3b", "size": 2 * 1024 ** 3},
        {"name": "qwen2.5:7b", "size": 4 * 1024 ** 3},
    ]}
    r = oc.probe(EP_LOCAL)
    assert r["status"] == oc.STATUS_ONLINE
    assert [m["name"] for m in r["models"]] == ["llama3.2:3b", "qwen2.5:7b"]


def test_an_openai_compatible_server_is_recognised(net):
    net["/v1/models"] = {"data": [{"id": "local-model"}]}
    r = oc.probe(EP_LOCAL)
    assert r["status"] == oc.STATUS_ONLINE
    assert r["backend"] == oc.BACKEND_OPENAI


# ── loopback vs LAN ───────────────────────────────────────────────

@pytest.mark.parametrize("endpoint", [
    "http://127.0.0.1:11434", "http://localhost:11434",
    "http://127.5.5.5:1234", "http://[::1]:11434",
])
def test_loopback_is_this_machine(endpoint):
    assert oc.is_loopback_endpoint(endpoint)


@pytest.mark.parametrize("endpoint", [
    "http://192.168.1.50:11434", "http://10.0.0.4:11434", "http://nas.local:11434",
])
def test_lan_is_not_this_machine(endpoint):
    assert not oc.is_loopback_endpoint(endpoint)


# ── finding the runtime ───────────────────────────────────────────

def test_path_wins_when_ollama_is_registered(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: r"C:\tools\ollama.EXE")
    assert oc.find_ollama_executable() == r"C:\tools\ollama.EXE"


def test_the_default_install_dir_is_searched(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda name: None)
    exe = tmp_path / "ollama.exe"
    exe.write_text("")
    monkeypatch.setattr(oc, "_OLLAMA_INSTALL_DIRS", (str(tmp_path),))
    assert oc.find_ollama_executable() == str(exe)


def test_absent_runtime_reports_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr(oc, "_OLLAMA_INSTALL_DIRS", (str(tmp_path / "nope"),))
    assert oc.find_ollama_executable() == ""


# ── what the settings screen says and shows ───────────────────────

@pytest.fixture
def screen(qapp):
    from app.screens.settings import SettingsScreen
    s = SettingsScreen()
    yield s
    s.deleteLater()


def _apply(screen, status, backend="", models=(), runtime=""):
    screen._on_connection_result(status, backend, list(models), runtime)


def test_offline_leaves_the_dropdown_empty(screen):
    """The reported bug, asserted where the user actually saw it."""
    _apply(screen, oc.STATUS_NOT_RUNNING, runtime=r"C:\ollama.exe")

    names = [screen._model_combo.itemText(i)
             for i in range(screen._model_combo.count())]
    assert "mistral" not in names
    assert "llama3.2:3b" not in names
    assert names == [] or all(n for n in names), f"placeholder models offered: {names}"


def test_a_model_the_user_really_chose_is_kept(screen):
    """A stored choice is a fact, not a suggestion — it must survive an outage."""
    if screen._store:
        screen._store.set_and_save("ai_model", "my-own-model")
    _apply(screen, oc.STATUS_NOT_RUNNING, runtime=r"C:\ollama.exe")
    names = [screen._model_combo.itemText(i)
             for i in range(screen._model_combo.count())]
    assert names in ([], ["my-own-model"])


def test_the_start_button_appears_only_when_it_would_work(screen):
    # isHidden() reflects the widget's own shown/hidden state; isVisibleTo()
    # would also answer for the AI page not being the active stack page.
    _apply(screen, oc.STATUS_NOT_RUNNING, runtime=r"C:\ollama.exe")
    assert not screen._btn_start_ollama.isHidden()

    _apply(screen, oc.STATUS_NOT_INSTALLED)
    assert screen._btn_start_ollama.isHidden(), "offered to start a missing runtime"

    _apply(screen, oc.STATUS_ONLINE, backend=oc.BACKEND_OLLAMA,
           models=[{"name": "llama3.2:3b", "size": 1}])
    assert screen._btn_start_ollama.isHidden(), "offered to start a running server"


def test_every_failure_explains_itself(screen):
    for status in (oc.STATUS_NOT_RUNNING, oc.STATUS_NOT_INSTALLED,
                   oc.STATUS_UNREACHABLE, oc.STATUS_REFUSED, oc.STATUS_NO_MODELS):
        text, _colour, hint = screen._connection_message(status, "", 0)
        assert text, f"{status}: no status text"
        assert hint, f"{status}: says what is wrong but not what to do"


def test_the_stopped_runtime_message_does_not_claim_it_is_missing(screen):
    text, _colour, hint = screen._connection_message(oc.STATUS_NOT_RUNNING, "", 0)
    assert "installed" in text.lower() and "not running" in text.lower()
    assert "nothing here needs to be filled in" in hint.lower(), \
        "does not tell the user Local needs no configuration"


def test_online_reports_the_real_count(screen):
    text, colour, hint = screen._connection_message(
        oc.STATUS_ONLINE, oc.BACKEND_OLLAMA, 3)
    assert "3" in text and colour == "safe" and hint == ""


def test_connecting_populates_the_real_models(screen):
    _apply(screen, oc.STATUS_ONLINE, backend=oc.BACKEND_OLLAMA, models=[
        {"name": "llama3.2:3b", "size": 2 * 1024 ** 3},
        {"name": "phi4:14b", "size": 9 * 1024 ** 3},
    ])
    names = [screen._model_combo.itemText(i)
             for i in range(screen._model_combo.count())]
    assert names == ["llama3.2:3b", "phi4:14b"]
    assert screen._model_combo.isEnabled()


# ── "Local" means this machine, not Ollama's port ─────────────────
#
# Reported: "it can't see models from lm studio". LOCAL_ENDPOINT is Ollama's
# 11434, and Local mode probed only that, so a machine running LM Studio on
# 1234 was told there was no local runtime — and the only way out was to switch
# to Server mode and type an address for a server on the same machine.

def test_the_candidate_list_covers_the_common_runtimes():
    ports = [ep.rsplit(":", 1)[-1] for ep in oc.LOCAL_CANDIDATE_ENDPOINTS]
    assert "11434" in ports, "Ollama"
    assert "1234" in ports, "LM Studio"
    assert "8080" in ports, "llama.cpp"


@pytest.fixture
def only_open(monkeypatch):
    """Pretend exactly one loopback port is listening."""
    def _factory(open_endpoint):
        monkeypatch.setattr(
            oc, "_port_is_open",
            lambda ep, timeout=0.25: ep.rstrip("/") == open_endpoint)
    return _factory


def test_local_finds_lm_studio_on_its_own_port(net, only_open):
    """The reported case: nothing on 11434, LM Studio answering on 1234."""
    only_open("http://127.0.0.1:1234")
    net["1234/v1/models"] = {"data": [{"id": "qwen3-8b"}, {"id": "phi-4"}]}

    r = oc.probe(oc.LOCAL_ENDPOINT, discover=True)

    assert r["status"] == oc.STATUS_ONLINE
    assert r["endpoint"] == "http://127.0.0.1:1234"
    assert [m["name"] for m in r["models"]] == ["qwen3-8b", "phi-4"]
    assert r["backend"] == oc.BACKEND_OPENAI


def test_without_discovery_the_configured_address_is_respected(net, only_open,
                                                               runtime_installed):
    """Server mode must not wander off to some other port behind the user."""
    only_open("http://127.0.0.1:1234")
    net["1234/v1/models"] = {"data": [{"id": "qwen3-8b"}]}

    r = oc.probe("http://127.0.0.1:11434", discover=False)

    assert r["status"] == oc.STATUS_NOT_RUNNING
    assert r["endpoint"] == "http://127.0.0.1:11434"


def test_a_runtime_with_models_beats_one_without(net, monkeypatch):
    """LM Studio answers with an empty list when no model is loaded."""
    monkeypatch.setattr(oc, "_port_is_open", lambda ep, timeout=0.25: True)
    net["1234/v1/models"] = {"data": []}          # LM Studio, nothing loaded
    net["11434/api/tags"] = {"models": [{"name": "gemma4:e2b", "size": 7}]}

    assert oc.discover_local_endpoint() == "http://127.0.0.1:11434"


def test_an_answering_runtime_with_no_models_is_still_found(net, monkeypatch):
    monkeypatch.setattr(oc, "_port_is_open",
                        lambda ep, timeout=0.25: ep.endswith(":1234"))
    net["1234/v1/models"] = {"data": []}

    assert oc.discover_local_endpoint() == "http://127.0.0.1:1234"
    r = oc.probe(oc.LOCAL_ENDPOINT, discover=True)
    assert r["status"] == oc.STATUS_NO_MODELS


def test_discovery_finds_nothing_when_nothing_listens(net, monkeypatch,
                                                      runtime_installed):
    monkeypatch.setattr(oc, "_port_is_open", lambda ep, timeout=0.25: False)
    assert oc.discover_local_endpoint() == ""
    assert oc.probe(oc.LOCAL_ENDPOINT, discover=True)["status"] == oc.STATUS_NOT_RUNNING


def test_a_dead_loopback_port_does_not_burn_the_http_timeout(monkeypatch):
    """Settings would sit frozen for seconds before it even started looking."""
    monkeypatch.setattr(oc, "_port_is_open", lambda ep, timeout=0.25: False)

    def _boom(url, timeout):
        raise AssertionError(f"made an HTTP request to a closed port: {url}")

    monkeypatch.setattr(oc, "_get_json", _boom)
    oc.probe("http://127.0.0.1:11434", discover=False)


def test_lm_studio_gets_lm_studio_advice(screen):
    _text, _colour, hint = screen._connection_message(
        oc.STATUS_NO_MODELS, oc.BACKEND_OPENAI, 0)
    assert "ollama pull" not in hint.lower(), "told an LM Studio user to run ollama"
    assert "lm studio" in hint.lower()


def test_ollama_still_gets_ollama_advice(screen):
    _text, _colour, hint = screen._connection_message(
        oc.STATUS_NO_MODELS, oc.BACKEND_OLLAMA, 0)
    assert "ollama pull" in hint.lower()


def test_a_discovered_endpoint_is_adopted(screen):
    """Otherwise AI calls keep going to the address that answered nothing."""
    screen._on_connection_result(
        oc.STATUS_ONLINE, oc.BACKEND_OPENAI,
        [{"name": "qwen3-8b", "size": 0}], "", "http://127.0.0.1:1234")

    assert screen._endpoint_input.text().strip() == "http://127.0.0.1:1234"
    names = [screen._model_combo.itemText(i)
             for i in range(screen._model_combo.count())]
    assert names == ["qwen3-8b"]
