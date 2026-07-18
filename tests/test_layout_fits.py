"""Translated strings must fit the controls they are drawn in.

Adding a language is not just translation — it is layout QA. Ukrainian alone
overflowed three controls (the sidebar nav, the scan button and the mode combo)
and had to be shortened, and that was found by eye in a screenshot. German
compounds are longer again, so this measures instead of hoping.

The check: for every fixed-width, non-wrapping control, compare the rendered
text width against the space the control actually has.
"""
import pytest
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QComboBox, QLabel, QPushButton

from app.config.settings_store import SettingsStore
from app.i18n import available_languages, set_language, get_language
from app.state.scan_state import ScanState
from app.themes.theme_manager import build_qss

# Horizontal padding a control reserves for borders/margins before its text.
# Measured against the app's own styling; a control tighter than this reads as
# cramped even when it technically fits.
_CHROME_PX = 16
_QWIDGETSIZE_MAX = 16777215  # Qt's 'no maximum' sentinel


@pytest.fixture
def app(qapp):
    from app.fonts import load_fonts
    load_fonts()
    qapp.setStyleSheet(build_qss("forest"))
    original = get_language()
    yield qapp
    set_language(original)


def _overflowing(widget) -> list[str]:
    """Names of descendants whose text cannot fit their own width."""
    bad = []
    # PySide6's findChildren takes one type, not a tuple.
    kids = []
    for cls in (QLabel, QPushButton, QComboBox):
        kids += widget.findChildren(cls)
        if isinstance(widget, cls):
            kids.append(widget)
    for w in kids:
        if not w.isVisible() and not w.isVisibleTo(widget):
            continue
        if isinstance(w, QComboBox):
            text = w.currentText()
        else:
            text = w.text()
            # A wrapping label is allowed to be wider than one line.
            if isinstance(w, QLabel) and w.wordWrap():
                continue
        if not text.strip():
            continue
        # Only controls with a COMMITTED width can truly overflow. A label sized
        # by its layout is given its natural width (needed == width is normal),
        # and an expanding control simply grows; flagging those buries the real
        # cases in noise.
        capped = w.maximumWidth() < _QWIDGETSIZE_MAX
        fixed = w.minimumWidth() == w.maximumWidth() and capped
        if not (capped or fixed):
            continue
        width = w.maximumWidth()
        if width <= 0:
            continue
        # Buttons and combos reserve padding for borders and the drop-down
        # arrow before any text is drawn; a bare label does not.
        allowance = _CHROME_PX if isinstance(w, (QPushButton, QComboBox)) else 0
        needed = QFontMetrics(w.font()).horizontalAdvance(text)
        if needed > width - allowance:
            bad.append(f"{type(w).__name__} {text!r} needs {needed}px, has {width}px")
    return bad


def _build_screens(app):
    """The screens with the tightest fixed-width chrome."""
    from app.screens.analyze import AnalyzeScreen
    from app.screens.settings import SettingsScreen
    from app.widgets.sidebar import Sidebar

    store = SettingsStore()
    state = ScanState()
    state.set_settings_store(store)

    analyze = AnalyzeScreen()
    analyze.set_scan_state(state)
    analyze.resize(1400, 900)

    settings = SettingsScreen(theme_callback=lambda _k: None, settings_store=store)
    settings.resize(1400, 900)

    sidebar = Sidebar()
    sidebar.resize(196, 900)

    out = []
    for w in (analyze, settings, sidebar):
        w.show()
        app.processEvents()
        out.append(w)
    return out


@pytest.mark.parametrize("language", available_languages())
def test_no_control_overflows_in_any_shipped_language(app, language):
    set_language(language)
    problems = []
    for screen in _build_screens(app):
        problems += _overflowing(screen)
    assert not problems, (
        f"{language}: text does not fit its control —\n  " + "\n  ".join(problems))


def test_the_harness_can_actually_detect_an_overflow(app):
    """A check that never fails is worthless — prove it catches a real case."""
    btn = QPushButton("A" * 200)
    btn.setFixedWidth(60)
    btn.show()
    app.processEvents()
    assert _overflowing(btn.parentWidget() or btn) or _overflowing(btn), (
        "harness failed to flag obviously overflowing text")


def test_every_offered_language_has_its_locale_file():
    """The picker is built from files on disk, so it cannot advertise a
    language that does not ship."""
    from app.i18n import LANGUAGES, _locales_dir
    for name in available_languages():
        code = LANGUAGES[name]
        if code == "en":
            continue
        assert (_locales_dir() / f"{code}.json").exists()
