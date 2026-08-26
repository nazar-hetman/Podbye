"""AI endpoint mode toggle — Local vs custom Server, and IP retention.

The important guarantee: a hand-typed server address survives a round-trip
through Local mode. It is stored in its own setting rather than being
overwritten when the active endpoint is repointed at localhost.
"""
import pytest

from app.config.settings_store import SettingsStore
from app.services.ollama_client import LOCAL_ENDPOINT


@pytest.fixture
def screen(tmp_path, monkeypatch):
    """A settings screen backed by a throwaway config file.

    _config_path is a MODULE-level function, not a method — patching it on the
    class silently does nothing and the test then writes to the user's real
    %APPDATA%\\Podbye\\config.json. Patch the module attribute.
    """
    from app.screens.settings import SettingsScreen
    import app.config.settings_store as ss
    cfg = tmp_path / "config.json"
    monkeypatch.setattr(ss, "_config_path", lambda: cfg)
    store = SettingsStore()
    assert str(store.config_path) == str(cfg), "settings store not isolated"
    return SettingsScreen(theme_callback=lambda _k: None, settings_store=store), store


SERVER = "http://192.168.1.50:11434"


def test_defaults_to_local(screen):
    s, store = screen
    assert s._rb_ep_local.isChecked()
    assert not s._endpoint_input.isEnabled(), "local endpoint must not be editable"
    assert store.get("ai_endpoint_mode") == "local"


def test_switching_to_server_enables_the_field(screen):
    s, _ = screen
    s._rb_ep_server.setChecked(True)
    assert s._endpoint_input.isEnabled()


def test_server_address_survives_a_local_round_trip(screen):
    """The reported requirement: don't lose the IP when toggling back."""
    s, store = screen
    s._rb_ep_server.setChecked(True)
    s._endpoint_input.setText(SERVER)
    s._on_endpoint_edited()
    assert store.get("ai_endpoint") == SERVER
    assert store.get("ai_server_endpoint") == SERVER

    # Back to Local — active endpoint repoints, remembered address is kept.
    s._rb_ep_local.setChecked(True)
    assert store.get("ai_endpoint") == LOCAL_ENDPOINT
    assert store.get("ai_server_endpoint") == SERVER, "server IP was lost"

    # Back to Server — the typed address returns.
    s._rb_ep_server.setChecked(True)
    assert s._endpoint_input.text() == SERVER
    assert store.get("ai_endpoint") == SERVER


def test_local_mode_does_not_clobber_saved_server_endpoint(screen):
    s, store = screen
    s._rb_ep_server.setChecked(True)
    s._endpoint_input.setText(SERVER)
    s._on_endpoint_edited()
    for _ in range(3):  # repeated toggling must stay stable
        s._rb_ep_local.setChecked(True)
        s._rb_ep_server.setChecked(True)
    assert store.get("ai_server_endpoint") == SERVER


def test_mode_and_address_restore_on_reload(screen, tmp_path, monkeypatch):
    """A saved Server setup comes back as Server with its address after restart."""
    s, store = screen
    s._rb_ep_server.setChecked(True)
    s._endpoint_input.setText(SERVER)
    s._on_endpoint_edited()

    from app.screens.settings import SettingsScreen
    fresh = SettingsScreen(theme_callback=lambda _k: None, settings_store=store)
    assert fresh._rb_ep_server.isChecked()
    assert fresh._endpoint_input.text() == SERVER
    assert fresh._endpoint_input.isEnabled()


def test_reload_in_local_mode_keeps_field_disabled(screen, tmp_path):
    s, store = screen
    from app.screens.settings import SettingsScreen
    fresh = SettingsScreen(theme_callback=lambda _k: None, settings_store=store)
    assert fresh._rb_ep_local.isChecked()
    assert not fresh._endpoint_input.isEnabled()
    assert fresh._endpoint_input.text() == LOCAL_ENDPOINT
