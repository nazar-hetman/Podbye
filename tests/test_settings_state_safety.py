"""Settings must be honest about durable changes and active work."""

from PySide6.QtWidgets import QMessageBox

from app.config.settings_store import SettingsStore
from app.screens.settings import SettingsScreen
from app.services import keep_list


def _store(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    keep_list.reset_for_tests()
    return SettingsStore()


def test_reset_keeps_durable_cleanup_exclusions(qapp, tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    kept = r"E:\\Example Project"
    store.set_and_save("kept_paths", [kept])
    keep_list.set_store(store)
    screen = SettingsScreen(settings_store=store)
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *args, **kwargs: QMessageBox.Yes))

    screen._reset_all_settings()

    assert store.get("kept_paths") == [kept]
    assert keep_list.is_kept(kept + r"\build\output.bin")
    screen.deleteLater()


def test_reset_is_blocked_while_analysis_or_ai_is_active(qapp, tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    store.set_and_save("ai_model", "qwen3")
    screen = SettingsScreen(settings_store=store, is_busy_callback=lambda: True)
    seen = {}
    monkeypatch.setattr(QMessageBox, "information", staticmethod(
        lambda _parent, title, body: seen.update(title=title, body=body)))

    screen._reset_all_settings()

    assert seen["title"] == "Reset unavailable"
    assert store.get("ai_model") == "qwen3"
    screen.deleteLater()


def test_save_failure_is_visible_in_settings(qapp):
    class Store:
        config_path = r"C:\\blocked\\config.json"

        values = {
            "ai_endpoint_mode": "local",
            "ai_endpoint": "http://127.0.0.1:11434",
            "ai_timeout": 180,
            "ai_max_concurrent": 3,
            "ai_tone": "Neutral",
            "ai_length": "Standard",
            "ai_explanation_language": "English",
            "ai_findings_enabled": False,
            "ai_startups_enabled": True,
            "ai_explain_risky_only": False,
            "perm_delete_enabled": False,
            "confirm_risky_cleanup": True,
            "scan_cross_volumes": False,
            "close_behavior": "ask",
            "ui_language": "English",
            "theme": "forest",
            "ai_model": "",
        }

        def get(self, key, default=None):
            return self.values.get(key, default)

        def set_and_save(self, _key, _value):
            return False

    screen = SettingsScreen(settings_store=Store())
    assert screen._save_value("ai_tone", "Technical") is False
    assert not screen._persistence_error_lbl.isHidden()
    assert r"C:\\blocked\\config.json" in screen._persistence_error_lbl.text()
    screen.deleteLater()


def test_settings_store_reports_a_real_write_failure(tmp_path, monkeypatch):
    import app.config.settings_store as settings_store

    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x")
    monkeypatch.setattr(settings_store, "_config_path", lambda: blocker / "config.json")
    store = SettingsStore()

    assert store.set_and_save("theme", "paper") is False
    assert store.last_save_error


def test_public_endpoint_is_rejected_before_it_is_saved(qapp, tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    screen = SettingsScreen(settings_store=store)
    screen._rb_ep_server.setChecked(True)
    screen._endpoint_input.setText("http://192.168.1.50:11434")
    screen._on_endpoint_edited()

    screen._endpoint_input.setText("https://api.example.com/v1")
    screen._on_endpoint_edited()

    assert store.get("ai_endpoint") == "http://192.168.1.50:11434"
    assert store.get("ai_server_endpoint") == "http://192.168.1.50:11434"
    assert screen._endpoint_input.text() == "http://192.168.1.50:11434"
    screen.deleteLater()


def test_pending_slider_value_is_saved_when_settings_closes(qapp, tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    screen = SettingsScreen(settings_store=store)
    screen._timeout_slider.setValue(240)
    assert any(timer.isActive() for timer in screen._slider_timers)

    screen.stop_background_work()

    assert store.get("ai_timeout") == 240
    screen.deleteLater()
