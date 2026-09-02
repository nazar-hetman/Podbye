"""About links to the repository — and that is all it does.

The link was asked for as "check for updates". The button is there, but what it
does is hand a URL to the system browser. Podbye making the request itself would
contradict the sentence this very panel closes with ("No cloud processing. No
background telemetry") and would announce the user's IP, version and launch
time to a server on every start — for a tool whose whole argument is that it is
the one that does not.
"""
import ast
import pathlib

import pytest

from app.version import REPO_URL, RELEASES_URL

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_the_urls_point_at_this_project():
    assert REPO_URL.startswith("https://github.com/")
    assert REPO_URL.rstrip("/").endswith("/Podbye")
    assert RELEASES_URL == REPO_URL + "/releases"


def test_the_repo_url_matches_the_git_remote():
    """Two copies of an address always drift."""
    config = (ROOT / ".git" / "config").read_text(encoding="utf-8")
    assert REPO_URL.removeprefix("https://").rstrip("/").lower() in \
        config.replace(".git", "").lower(), "About points somewhere else than origin"


def test_nothing_fetches_those_urls():
    """The guarantee, stated as a test rather than as a comment."""
    banned = {"urllib", "urllib.request", "requests", "httpx", "http", "socket"}
    src = (ROOT / "app" / "screens" / "settings.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not (imported & banned), f"settings.py reaches the network: {imported & banned}"


def test_no_automatic_update_check_anywhere():
    """A version-comparison endpoint would be a phone-home by another name."""
    hits = []
    for path in (ROOT / "app").rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        text = path.read_text(encoding="utf-8", errors="replace").lower()
        for probe in ("api.github.com", "releases/latest", "update_check",
                      "check_for_update"):
            if probe in text:
                hits.append(f"{path.name}: {probe}")
    assert not hits, f"looks like an in-app update check: {hits}"


def test_the_button_opens_a_browser_rather_than_requesting(qapp, monkeypatch):
    from app.screens.settings import SettingsScreen

    opened = []
    import PySide6.QtGui as qtgui
    monkeypatch.setattr(qtgui.QDesktopServices, "openUrl",
                        staticmethod(lambda url: opened.append(url.toString())))

    screen = SettingsScreen()
    try:
        screen._open_external(RELEASES_URL)
        assert opened == [RELEASES_URL]
    finally:
        screen.close()
        screen.deleteLater()
        qapp.processEvents()


def test_about_actually_shows_the_buttons(qapp):
    from PySide6.QtWidgets import QPushButton
    from app.screens.settings import SettingsScreen

    screen = SettingsScreen()
    try:
        labels = {b.text() for b in screen.findChildren(QPushButton)}
        assert "Check for updates" in labels
        assert "View source" in labels
        tips = {b.toolTip() for b in screen.findChildren(QPushButton)}
        assert RELEASES_URL in tips and REPO_URL in tips, \
            "the button should say where it is sending you"
    finally:
        screen.close()
        screen.deleteLater()
        qapp.processEvents()


def test_a_store_set_after_construction_still_shows_its_path(qapp):
    """About caches the config path while building. main.py passes the store to
    __init__ and never hits this, but the setter is public and the row failed
    silently — "unavailable", with a dead Open folder button."""
    from app.config.settings_store import SettingsStore
    from app.screens.settings import SettingsScreen

    screen = SettingsScreen()                      # built with no store...
    try:
        store = SettingsStore()
        screen.set_settings_store(store)           # ...store arrives after
        assert screen._storage_targets["config"] == str(store.config_path)
        # full_text(), not text(): the row shows the path in an ElidedLabel,
        # which middle-elides a real config path at this width and returns
        # what survived. The value under it is what this is about.
        assert screen._config_path_lbl.full_text() == str(store.config_path)
    finally:
        screen.close()
        screen.deleteLater()
        qapp.processEvents()
