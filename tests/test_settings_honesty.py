"""Settings must not show the user something that is not true.

Reported: "I can't use ollama via app." Ollama was healthy — running, one model
pulled, /api/generate answering. Podbye's own probe reported "online · Ollama ·
1 model available" in green. Every explanation still failed.

The stored ai_model was "gemma2:2b", a model that had been removed from the
server. Repopulating the dropdown searched for the saved name, found nothing,
and fell through: the combo displayed the first installed model while the config
still pointed at the missing one. Settings looked healthy from every angle, and
the only evidence was a per-item ai_error nobody reads.

Three neighbouring faults are covered here too, all the same shape — the screen
stating something the code does not back up:

  * paths in About that no code has ever created (reports, cache/hashes.db) and
    a logs folder that is always empty because logging is console-only;
  * "small • local model" printed for every LM Studio model, which reports no
    size at all, so a 70B model was labelled small;
  * sliders that only persisted on mouse release, so a value set with the
    keyboard moved on screen and was gone at the next launch.
"""
import json
import os

import pytest

from app.config.settings_store import SettingsStore
from app.screens.settings import SettingsScreen, _dir_size, _human_size


OLLAMA_MODELS = [{"name": "gemma4:e2b", "size": 7162405886, "modified": ""}]
LMSTUDIO_MODELS = [{"name": "qwen3-70b", "size": 0, "modified": ""}]


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A SettingsStore writing into a throwaway APPDATA."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    return SettingsStore()


@pytest.fixture
def screen(store):
    return SettingsScreen(settings_store=store)


def _connected(screen, models, backend="ollama"):
    screen._on_connection_result("online", backend, models, "",
                                 "http://127.0.0.1:11434")


# ── the model the config points at must be one that exists ───────

def test_missing_saved_model_is_replaced_by_one_that_exists(store, screen):
    store.set_and_save("ai_model", "gemma2:2b")

    _connected(screen, OLLAMA_MODELS)

    assert store.get("ai_model") == "gemma4:e2b"
    assert screen._model_combo.currentText() == "gemma4:e2b"


def test_the_swap_is_stated_not_silent(store, screen):
    store.set_and_save("ai_model", "gemma2:2b")

    _connected(screen, OLLAMA_MODELS)

    hint = screen._conn_hint_lbl.text()
    assert "gemma2:2b" in hint and "gemma4:e2b" in hint
    assert not screen._conn_hint_lbl.isHidden()


def test_a_model_that_is_still_installed_is_left_alone(store, screen):
    store.set_and_save("ai_model", "gemma4:e2b")

    _connected(screen, OLLAMA_MODELS)

    assert store.get("ai_model") == "gemma4:e2b"
    assert screen._conn_hint_lbl.isHidden(), "nothing changed, so say nothing"


def test_first_connection_adopts_a_model_without_nagging(store, screen):
    """No saved choice yet: pick one, save it, but do not report a 'swap'."""
    store.set_and_save("ai_model", "")

    _connected(screen, OLLAMA_MODELS)

    assert store.get("ai_model") == "gemma4:e2b"
    assert screen._conn_hint_lbl.isHidden()


def test_an_offline_server_never_discards_the_saved_model(store, screen):
    """Empty list means "cannot see the server", not "your model is gone"."""
    store.set_and_save("ai_model", "gemma4:e2b")

    screen._on_connection_result("not_running", "", [], "", "")

    assert store.get("ai_model") == "gemma4:e2b"


# ── size labels: no invented numbers ──────────────────────────────

def test_lm_studio_models_are_not_all_called_small(screen):
    """OpenAI-compatible servers report no size. Saying "small" invents one."""
    _connected(screen, LMSTUDIO_MODELS, backend="openai")

    meta = screen._model_meta_lbl.text()
    assert "small" not in meta.lower()
    assert "not reported" in meta


def test_ollama_size_is_still_shown_when_the_server_gives_one(screen):
    _connected(screen, OLLAMA_MODELS)

    assert "6.7 GB" in screen._model_meta_lbl.text()


def test_an_unverified_model_claims_nothing_about_its_size(store):
    """Before any server is contacted the size is unknown — which is not the
    same as a server answering that it has no size to report."""
    store.set_and_save("ai_model", "gemma4:e2b")

    screen = SettingsScreen(settings_store=store)

    assert screen._model_meta_lbl.text() == ""


# ── About lists directories that exist, and only those ────────────

def test_about_reports_the_real_storage_locations(screen, store):
    targets = screen._storage_targets

    assert targets["config"] == store.config_path

    from app.state.session_store import sessions_dir
    from app.services.ai_explainer import cache_dir

    assert targets["sessions"] == str(sessions_dir())
    assert targets["ai_cache"] == str(cache_dir())


def test_about_no_longer_advertises_folders_nothing_creates(screen):
    """"reports" and "cache\\hashes.db" were displayed but never created.

    Asserted against what the panel shows, not against the source text — the
    comment recording why they were removed legitimately names them.
    """
    shown = " ".join(screen._storage_targets.values()).lower()

    assert "hashes.db" not in shown
    assert "reports" not in shown


def test_every_path_about_shows_comes_from_the_code_that_owns_it(screen):
    """The fictional entries were hardcoded literals. Reading the real accessor
    is what makes a repeat impossible."""
    import app.screens.settings as mod

    with open(mod.__file__, encoding="utf-8") as fh:
        source = fh.read()

    assert "from app.state.session_store import sessions_dir" in source
    assert "from app.services.ai_explainer import cache_dir" in source


def test_the_session_store_is_listed_because_it_is_the_big_one(screen):
    """It has reached gigabytes in practice and used to be the one omitted."""
    assert "sessions" in screen._storage_targets
    assert "sessions" in screen._storage_size_lbls


def test_no_button_offers_to_open_a_logs_folder_that_stays_empty():
    """Logging is console-only, so the button created an empty folder."""
    assert not hasattr(SettingsScreen, "_open_logs_folder")


# ── storage measurement ───────────────────────────────────────────

def test_dir_size_counts_bytes_and_files(tmp_path):
    (tmp_path / "a.json").write_bytes(b"x" * 100)
    nested = tmp_path / "sub"
    nested.mkdir()
    (nested / "b.json").write_bytes(b"y" * 50)

    assert _dir_size(str(tmp_path)) == (150, 2)


def test_dir_size_of_a_missing_folder_is_not_an_error(tmp_path):
    assert _dir_size(str(tmp_path / "never-created")) == (0, 0)


@pytest.mark.parametrize("size,expected", [
    (0, "0 B"),
    (2048, "2 KB"),
    (5 * 1024 ** 2, "5 MB"),
    (3 * 1024 ** 3, "3.0 GB"),
])
def test_human_size_uses_a_unit_a_person_can_read(size, expected):
    assert _human_size(size) == expected


def test_clearing_the_ai_cache_removes_cached_answers(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    from app.services import ai_explainer

    cache = ai_explainer.cache_dir()
    cache.mkdir(parents=True)
    (cache / "aaa.json").write_text(json.dumps({"explanation": "hi"}))
    (cache / "bbb.json").write_text(json.dumps({"explanation": "there"}))

    assert ai_explainer.clear_cache() == 2
    assert list(cache.glob("*.json")) == []


def test_clearing_an_absent_cache_is_a_no_op(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "nothing-here"))
    from app.services import ai_explainer

    assert ai_explainer.clear_cache() == 0


# ── sliders persist however they were moved ───────────────────────

def test_a_slider_moved_by_keyboard_is_saved(store, screen):
    """sliderReleased only fires for a mouse drag; arrow keys were lost."""
    screen._timeout_slider.setValue(240)

    for timer in screen._slider_timers:
        if timer.isActive():
            timer.stop()
            timer.timeout.emit()

    assert store.get("ai_timeout") == 240


def test_loading_settings_does_not_write_them_back(tmp_path, monkeypatch):
    """Restoring a saved value must not look like a user edit."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    store = SettingsStore()
    store.set_and_save("ai_timeout", 120)
    SettingsScreen(settings_store=store)

    assert not any(t.isActive() for t
                   in SettingsScreen(settings_store=store)._slider_timers)


# ── Podbye protects all of its own data, not half of it ────────────

def test_both_appdata_roots_are_protected_from_cleanup(tmp_path, monkeypatch):
    """The AI cache lives under LOCALAPPDATA and used to be unprotected, so a
    full C:/ scan could offer to delete Podbye's own data while it ran."""
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    from app.services import self_paths

    assert self_paths.is_self_path(
        os.path.join(str(tmp_path / "local"), "Podbye", "cache", "ai"))
    assert self_paths.is_self_path(
        os.path.join(str(tmp_path / "roaming"), "Podbye", "sessions"))


# ── no control that can never be used ─────────────────────────────

def test_the_permanent_delete_radio_is_gone(screen):
    """It was permanently disabled under "Not available yet" — dead UI on the
    one screen where the user decides how much to trust cleanup."""
    assert not hasattr(screen, "_rb_permanent")
    assert not hasattr(screen, "_rb_recycle")


def test_ai_explanation_language_is_not_limited_to_shipped_locales():
    """What the model writes has nothing to do with Podbye's own translations."""
    from app.i18n import available_languages, explanation_languages

    assert set(available_languages()) <= set(explanation_languages())
    assert "German" in explanation_languages()
