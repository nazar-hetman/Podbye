"""The About page: a maintenance panel that admits what it is, and a reset
that asks before it acts.

The panel was called Diagnostics and contained nothing that reports on a
fault — one destructive action and the product metadata under it. The action
itself was a filled red button that fired on a single click with no prompt.
"""
import pytest
from PySide6.QtWidgets import QLabel, QMessageBox, QPushButton

from app.screens.settings import SettingsScreen, _PATH_VALUE_WIDTH
from app.themes import theme_manager as tm


@pytest.fixture
def about(qapp):
    s = SettingsScreen()
    s.resize(1200, 900)
    s.show()
    s._switch_section("about")
    qapp.processEvents()
    qapp.processEvents()
    yield s, s._stack.currentWidget()
    s.deleteLater()


def _texts(root):
    return [l.text() for l in root.findChildren(QLabel)]


# ── the panel says what it holds ──────────────────────────────────

def test_the_panel_is_no_longer_called_diagnostics(about):
    s, page = about
    titles = _texts(page)
    assert "Diagnostics" not in titles
    assert "Maintenance" in titles


def test_build_and_storage_are_untouched(about):
    s, page = about
    titles = _texts(page)
    assert "Build" in titles
    assert "Storage" in titles


# ── a path is one value ───────────────────────────────────────────

@pytest.mark.parametrize("width", [904, 1100, 1200, 1600])
def test_a_real_windows_path_stays_on_one_line(qapp, width):
    """904px is roughly the settings area inside the app's 1100px minimum
    window. At 380px the sessions path broke in two, splitting one value at
    whatever character landed on the boundary.

    The label is given a realistic path rather than the one it renders: the
    suite redirects APPDATA into a pytest temp directory, so the live value is
    a good deal longer than anything a user has. A path that outruns the column
    still wraps - that is the "where space allows" part - but the ordinary case
    must not.
    """
    real = "C:" + chr(92) + chr(92).join(
        ["Users", "Nazar", "AppData", "Roaming", "Podbye", "sessions"])
    s = SettingsScreen()
    s.resize(width, 900)
    s.show()
    s._switch_section("about")
    qapp.processEvents()
    qapp.processEvents()
    try:
        page = s._stack.currentWidget()
        path = next(l for l in page.findChildren(QLabel)
                    if "Podbye" in l.text() and ":" in l.text())
        path.setText(real)
        path.updateGeometry()
        for _ in range(3):
            qapp.processEvents()
        lines = round(path.height() / path.fontMetrics().lineSpacing())
        assert lines == 1, f"{real!r} wrapped to {lines} lines at {width}px"
    finally:
        s.deleteLater()


def test_the_path_column_is_a_minimum_not_a_cap(about):
    """A word-wrapping QLabel reports a narrow size hint, so a maximumWidth
    alone left the label at 484px and the path still wrapped."""
    s, page = about
    path = next(l for l in page.findChildren(QLabel)
                if "Podbye" in l.text() and ":" in l.text())
    assert path.minimumWidth() == _PATH_VALUE_WIDTH


def test_the_path_stays_selectable(about):
    """It is a value someone copies into Explorer."""
    from PySide6.QtCore import Qt
    s, page = about
    path = next(l for l in page.findChildren(QLabel)
                if "Podbye" in l.text() and ":" in l.text())
    assert path.textInteractionFlags() & Qt.TextSelectableByMouse


# ── reset is quiet until you reach for it ─────────────────────────

def test_reset_is_not_a_filled_red_button_at_rest(about):
    """#Danger paints a solid red fill. Analyze's Stop button uses that name,
    and the weight belongs there."""
    s, _page = about
    assert s._btn_reset.objectName() != "Danger"
    qss = s._btn_reset.styleSheet()
    rest = qss.split(":hover")[0]
    assert "background: transparent" in rest


@pytest.mark.parametrize("theme", ["forest", "amber", "mono", "paper"])
def test_reset_turns_dangerous_on_hover_and_focus(about, theme):
    s, _page = about
    tm._current_theme_key = theme
    s._restyle_reset_button()
    qss = s._btn_reset.styleSheet()
    risk = tm.PALETTES[theme]["risk"]
    hover = qss.split(":hover")[1].split("}")[0]
    focus = qss.split(":focus")[1].split("}")[0]
    assert risk in hover, f"{theme}: hover carries no danger colour"
    assert risk in focus, f"{theme}: keyboard focus carries no danger colour"


# ── and it asks first ─────────────────────────────────────────────

class _Store:
    def __init__(self):
        self.reset_called = False

    def reset(self):
        self.reset_called = True

    def get(self, key, default=None):
        return default


@pytest.fixture
def resettable(about, monkeypatch):
    s, _page = about
    s._store = _Store()
    monkeypatch.setattr(s, "_load_from_store", lambda: None)
    s._theme_callback = None
    return s


def test_declining_the_prompt_changes_nothing(resettable, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.No))
    resettable._reset_all_settings()
    assert not resettable._store.reset_called


def test_accepting_the_prompt_resets(resettable, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.Yes))
    resettable._reset_all_settings()
    assert resettable._store.reset_called


def test_the_prompt_defaults_to_not_resetting(resettable, monkeypatch):
    """A stray Enter must not wipe every preference."""
    seen = {}

    def _spy(parent, title, text, buttons, default=None):
        seen["default"] = default
        seen["text"] = text
        return QMessageBox.No

    monkeypatch.setattr(QMessageBox, "question", staticmethod(_spy))
    resettable._reset_all_settings()
    assert seen["default"] == QMessageBox.No


def test_the_prompt_says_what_survives(resettable, monkeypatch):
    """Scan history and kept paths are not part of a settings reset, and the
    user should not have to find that out by trying it."""
    seen = {}
    monkeypatch.setattr(QMessageBox, "question", staticmethod(
        lambda parent, title, text, buttons, default=None:
            (seen.__setitem__("text", text), QMessageBox.No)[1]))
    resettable._reset_all_settings()
    assert "kept paths" in seen["text"].lower()


# ── subdued metadata still has to be readable ─────────────────────

def _luminance(hex_color):
    h = hex_color.lstrip("#")
    ch = []
    for i in (0, 2, 4):
        v = int(h[i:i + 2], 16) / 255
        ch.append(v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4)
    return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]


def _contrast(a, b):
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("theme", ["forest", "amber", "mono", "paper"])
def test_helper_text_is_readable_on_every_theme(about, theme):
    """The local-first line, the Qt credit and the storage sizes all use
    _helper_style. It used text_faint — the colour of a disabled control —
    and measured 3.3-3.9 on every theme."""
    s, _page = about
    tm._current_theme_key = theme
    style = s._helper_style()
    colour = style.split("color:")[1].strip().rstrip(";").strip()
    p = tm.PALETTES[theme]
    for surface in ("panel_alt", "tint_bg"):
        ratio = _contrast(colour, p[surface])
        assert ratio >= 4.5, f"{theme}: helper text {colour} on {surface} is {ratio:.2f}"


def test_helper_text_stays_smaller_than_a_description(about):
    """Subdued by size, not by fading it out of legibility."""
    s, _page = about
    assert "font-size: 10px" in s._helper_style()
