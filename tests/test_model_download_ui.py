"""AI Settings: what the download row shows, and what it saves.

No Ollama, no network, no threads: pull_model is replaced per test, and the
handlers are driven directly so the assertions are about state transitions
rather than about timing.
"""
import pytest

from app.screens.settings import SettingsScreen
from app.services import ollama_client as oc


class _Store(dict):
    """The settings-store surface these handlers use."""

    def get(self, key, default=None):
        return super().get(key, default)

    def set(self, key, value):
        self[key] = value

    def set_and_save(self, key, value):
        self[key] = value
        return True


@pytest.fixture
def ai(qapp):
    store = _Store({"ai_model": "old:1b"})
    s = SettingsScreen()
    s._store = store
    s.resize(1200, 900)
    s.show()
    s._switch_section("ai")
    qapp.processEvents()
    yield s, store
    s.deleteLater()
    qapp.processEvents()


def _library(screen, *names):
    screen._model_combo.blockSignals(True)
    try:
        screen._model_combo.clear()
        for n in names:
            screen._model_combo.addItem(n, 0)
    finally:
        screen._model_combo.blockSignals(False)


# ── the row exists and starts idle ────────────────────────────────

def test_the_download_row_starts_quiet(ai):
    s, _ = ai
    assert s._pull_input.text() == ""
    assert not s._pull_bar.isVisible()
    assert not s._btn_cancel_pull.isVisible()
    assert s._btn_download.isEnabled()


def test_nothing_downloads_without_a_press(ai, monkeypatch):
    """The whole point of the row: no automatic download, ever."""
    called = []
    monkeypatch.setattr(oc, "pull_model", lambda *a, **k: called.append(1))
    s, _ = ai
    s._switch_section("ai")
    assert called == []


# ── input validation ──────────────────────────────────────────────

def test_a_malformed_id_never_starts_a_download(ai, monkeypatch):
    called = []
    monkeypatch.setattr(oc, "pull_model", lambda *a, **k: called.append(1))
    s, _ = ai
    s._pull_input.setText("not a model")
    s._start_pull()
    assert called == []
    assert "not a model id" in s._pull_status_lbl.text().lower()
    assert s._btn_download.isEnabled(), "the row stays usable after a typo"


def test_an_already_installed_model_is_selected_not_downloaded(ai, monkeypatch):
    called = []
    monkeypatch.setattr(oc, "pull_model", lambda *a, **k: called.append(1))
    s, store = ai
    _library(s, "gemma3:4b", "mistral:7b")
    s._pull_input.setText("gemma3:4b")
    s._start_pull()
    assert called == [], "re-downloading what is already there is a no-op"
    assert "already installed" in s._pull_status_lbl.text().lower()
    assert s._model_combo.currentText() == "gemma3:4b"
    assert store["ai_model"] == "gemma3:4b"


# ── progress rendering ────────────────────────────────────────────

def test_progress_without_a_size_stays_indeterminate(ai):
    """Before the server sends a total, no percentage is invented."""
    s, _ = ai
    s._on_pull_progress("pulling manifest", 0, 0)
    assert s._pull_bar.maximum() == 0, "indeterminate, not 0%"
    assert s._pull_status_lbl.text() == "pulling manifest"


def test_progress_with_a_size_shows_real_bytes(ai):
    s, _ = ai
    s._on_pull_progress("pulling", 2 * 1024 ** 3, 4 * 1024 ** 3)
    assert s._pull_bar.maximum() == 100
    assert s._pull_bar.value() == 50
    text = s._pull_status_lbl.text()
    assert "2.0 GB" in text and "4.0 GB" in text


def test_the_bar_holds_its_fill_through_the_trailing_lines(ai):
    """"verifying" and "success" carry no size. Dropping back to a sweeping
    bar there reads as the download starting over.

    Regression: the first version asked the bar whether it had a size, but a
    fresh QProgressBar already reports a maximum of 100, so "no size yet" and
    "finished" looked identical.
    """
    s, _ = ai
    s._on_pull_progress("pulling", 4 * 1024 ** 3, 4 * 1024 ** 3)
    assert s._pull_bar.value() == 100
    s._on_pull_progress("verifying sha256 digest", 0, 0)
    assert s._pull_bar.maximum() == 100, "it went back to indeterminate"
    assert s._pull_bar.value() == 100
    assert s._pull_status_lbl.text() == "verifying sha256 digest"


# ── outcomes ──────────────────────────────────────────────────────

def test_success_refreshes_the_library_and_selects_the_model(ai, monkeypatch):
    s, store = ai
    refreshed = []
    monkeypatch.setattr(s, "_test_connection", lambda: refreshed.append(1))
    s._on_pull_done(oc.PULL_OK, "gemma3:4b")
    assert refreshed == [1], "the list is re-read from the server"
    assert s._pending_select_model == "gemma3:4b"
    assert not s._pull_bar.isVisible()
    assert "installed" in s._pull_status_lbl.text().lower()


def test_the_downloaded_model_is_selected_once_the_library_arrives(ai):
    """The two halves together: pending name + a refreshed list."""
    s, store = ai
    s._pending_select_model = "gemma3:4b"
    _library(s, "mistral:7b", "gemma3:4b")
    assert s._select_model("gemma3:4b") is True
    assert s._model_combo.currentText() == "gemma3:4b"
    assert store["ai_model"] == "gemma3:4b", "survives a restart"


def test_selecting_a_model_that_is_not_there_changes_nothing(ai):
    s, store = ai
    _library(s, "mistral:7b")
    assert s._select_model("gemma3:4b") is False
    assert store["ai_model"] == "old:1b"


@pytest.mark.parametrize("code,expected", [
    (oc.PULL_CANCELLED, "stopped"),
    (oc.PULL_OFFLINE,   "lost contact"),
    (oc.PULL_BAD_ID,    "not a model id"),
    (oc.PULL_REFUSED,   "local or lan"),
])
def test_each_failure_says_what_happened(ai, code, expected):
    s, _ = ai
    s._on_pull_done(code, "")
    assert expected in s._pull_status_lbl.text().lower()
    assert not s._pull_bar.isVisible()
    assert s._btn_download.isEnabled(), "the user can try again"


def test_a_failed_pull_reports_the_server_s_reason(ai):
    s, _ = ai
    s._on_pull_done(oc.PULL_FAILED, "pull model manifest: file does not exist")
    assert "does not exist" in s._pull_status_lbl.text()


def test_running_out_of_space_names_both_figures(ai):
    s, _ = ai
    s._on_pull_done(oc.PULL_NO_SPACE, f"{4 * 1024 ** 3}/{1024 ** 3}")
    text = s._pull_status_lbl.text()
    assert "4.0 GB" in text and "1.0 GB" in text
    assert "nothing was downloaded" in text.lower()


def test_a_failure_never_refreshes_the_library(ai, monkeypatch):
    """Only a completed download may change what the list claims is installed."""
    s, _ = ai
    refreshed = []
    monkeypatch.setattr(s, "_test_connection", lambda: refreshed.append(1))
    for code in (oc.PULL_FAILED, oc.PULL_CANCELLED, oc.PULL_OFFLINE,
                 oc.PULL_NO_SPACE, oc.PULL_BAD_ID, oc.PULL_REFUSED):
        s._on_pull_done(code, "0/0")
    assert refreshed == []


# ── cancelling ────────────────────────────────────────────────────

def test_cancel_sets_the_flag_the_worker_polls(ai):
    s, _ = ai
    s._pull_cancel = False
    s._cancel_pull()
    assert s._pull_cancel is True
    assert "stopping" in s._pull_status_lbl.text().lower()


def test_the_stop_button_only_appears_while_downloading(ai):
    s, _ = ai
    s._show_pull_state("working", busy=True)
    assert s._btn_cancel_pull.isVisible()
    assert not s._btn_download.isEnabled()
    s._show_pull_state("done", busy=False)
    assert not s._btn_cancel_pull.isVisible()
    assert s._btn_download.isEnabled()
