"""A rename must not read as data loss.

The product shipped as Vigil. Everything a person accumulated lives under that
name: their settings, the Keep list of folders they marked "never delete this",
every saved scan session, the AI answer cache. Pointing the new build at an
empty Podbye directory would present as all of it being gone.

These tests hold the three properties the migration has to have — it carries
everything over, it can run a thousand times, and it never lets older data
overwrite newer.
"""
import io
import json
import os

import pytest

from app.config.legacy_migration import CURRENT_NAME, LEGACY_NAME, migrate


@pytest.fixture
def appdata(tmp_path, monkeypatch):
    """Both AppData roots, redirected somewhere disposable."""
    roaming, local = tmp_path / "Roaming", tmp_path / "Local"
    roaming.mkdir()
    local.mkdir()
    monkeypatch.setenv("APPDATA", str(roaming))
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    return roaming, local


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    io.open(path, "w", encoding="utf-8").write(text)


def _seed_vigil(roaming, local):
    """What a real install looks like: settings, Keep marks, sessions, cache."""
    old = roaming / LEGACY_NAME
    _write(old / "config.json", json.dumps({
        "theme": "forest",
        "ui_language": "Ukrainian",
        "kept_paths": ["E:\\Projects\\Focus"],
    }))
    _write(old / "sessions" / "history.json", '{"entries": [1, 2, 3]}')
    _write(old / "sessions" / "session_b7ff978b.json", '{"entities": []}')
    _write(old / "sessions" / "last_run.json", '{"resume": true}')
    _write(old / "logs" / "podbye.log", "started\n")
    _write(local / LEGACY_NAME / "cache" / "ai" / "a1b2.json", '{"answer": "x"}')
    return old


# ── it carries everything over ────────────────────────────────────

def test_settings_survive_the_rename(appdata):
    roaming, local = appdata
    _seed_vigil(roaming, local)
    migrate()
    config = json.load(io.open(roaming / CURRENT_NAME / "config.json",
                               encoding="utf-8"))
    assert config["theme"] == "forest"
    assert config["ui_language"] == "Ukrainian"


def test_the_keep_list_survives_the_rename(appdata):
    """The one setting a person actively built by hand, folder by folder."""
    roaming, local = appdata
    _seed_vigil(roaming, local)
    migrate()
    config = json.load(io.open(roaming / CURRENT_NAME / "config.json",
                               encoding="utf-8"))
    assert config["kept_paths"] == ["E:\\Projects\\Focus"]


def test_every_saved_session_survives(appdata):
    roaming, local = appdata
    _seed_vigil(roaming, local)
    migrate()
    sessions = roaming / CURRENT_NAME / "sessions"
    assert {p.name for p in sessions.iterdir()} == {
        "history.json", "session_b7ff978b.json", "last_run.json"}


def test_the_ai_cache_in_the_other_appdata_root_comes_too(appdata):
    """Data was always split across Roaming and Local. Both roots move."""
    roaming, local = appdata
    _seed_vigil(roaming, local)
    migrate()
    assert (local / CURRENT_NAME / "cache" / "ai" / "a1b2.json").exists()


def test_nested_folders_keep_their_shape(appdata):
    roaming, local = appdata
    _seed_vigil(roaming, local)
    migrate()
    assert (roaming / CURRENT_NAME / "logs" / "podbye.log").read_text() \
        == "started\n"


def test_it_reports_what_it_carried(appdata):
    roaming, local = appdata
    _seed_vigil(roaming, local)
    result = migrate()
    assert result["moved"] == 6
    assert not result["failures"]


# ── it can run forever ────────────────────────────────────────────

def test_running_it_again_changes_nothing(appdata):
    """It runs on every single start, not just the first."""
    roaming, local = appdata
    _seed_vigil(roaming, local)
    migrate()
    before = json.load(io.open(roaming / CURRENT_NAME / "config.json",
                               encoding="utf-8"))
    second = migrate()
    after = json.load(io.open(roaming / CURRENT_NAME / "config.json",
                              encoding="utf-8"))
    assert second["moved"] == 0
    assert before == after


def test_a_clean_install_has_nothing_to_do(appdata):
    """The normal case, forever after the first launch."""
    result = migrate()
    assert result == {"moved": 0, "failures": [], "roots": []}


def test_it_leaves_the_old_folder_alone(appdata):
    """A one-way rename the user did not ask for. A folder they can delete
    themselves is a better failure mode than one Podbye deleted for them."""
    roaming, local = appdata
    old = _seed_vigil(roaming, local)
    migrate()
    assert old.is_dir()
    assert (old / "config.json").exists()


# ── newer data always wins ────────────────────────────────────────

def test_it_never_overwrites_newer_settings(appdata):
    """Ran the new build, changed a setting, then restored an old backup.
    The restored copy is older; it must not win."""
    roaming, local = appdata
    _seed_vigil(roaming, local)
    _write(roaming / CURRENT_NAME / "config.json",
           json.dumps({"theme": "midnight"}))

    migrate()

    config = json.load(io.open(roaming / CURRENT_NAME / "config.json",
                               encoding="utf-8"))
    assert config["theme"] == "midnight", "the newer file has to survive"


def test_a_half_populated_folder_still_gets_what_it_is_missing(appdata):
    """Per file, not per folder: a Podbye sessions folder holding one session
    must still receive the others."""
    roaming, local = appdata
    _seed_vigil(roaming, local)
    _write(roaming / CURRENT_NAME / "sessions" / "history.json", "{}")

    migrate()

    sessions = roaming / CURRENT_NAME / "sessions"
    assert (sessions / "session_b7ff978b.json").exists()
    assert (sessions / "last_run.json").exists()
    assert (sessions / "history.json").read_text() == "{}", "newer wins"


# ── it never stops the app starting ───────────────────────────────

def test_a_file_it_cannot_read_is_reported_not_raised(appdata, monkeypatch):
    """A locked session file is a nuisance. Failing to launch is not."""
    roaming, local = appdata
    _seed_vigil(roaming, local)

    import app.config.legacy_migration as mod
    real_copy = mod.shutil.copy2

    def flaky(src, dst, *a, **kw):
        if str(src).endswith("last_run.json"):
            raise PermissionError("file is open in another process")
        return real_copy(src, dst, *a, **kw)

    monkeypatch.setattr(mod.shutil, "copy2", flaky)

    result = migrate()

    assert len(result["failures"]) == 1
    assert "last_run.json" in result["failures"][0]
    assert (roaming / CURRENT_NAME / "config.json").exists(), \
        "one bad file must not stop the rest"


def test_no_appdata_at_all_is_survivable(tmp_path, monkeypatch):
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_path))
    assert migrate()["moved"] == 0


# ── the app actually calls it ─────────────────────────────────────

def test_startup_runs_the_migration_before_reading_settings():
    """A migration nothing calls is not a migration."""
    source = io.open("app/main.py", encoding="utf-8").read()
    assert "from app.config.legacy_migration import migrate" in source
    body = source[source.index("def main():"):]
    assert body.index("migrate(") < body.index("QApplication(sys.argv)")
