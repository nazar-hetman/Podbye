"""Every popup must be readable on any machine, whatever Windows is wearing.

Reported from a second PC: *"window with deletion is white and it's bad
readable — maybe because of the windows theme or something"*, and *"there are
no gap between buttons on the popup, buttons almost merged"*.

The white dialog is a build from before the fix — ``QDialog, QMessageBox`` got
an explicit background on 2026-08-18 at 10:17, and the beta.3 zip on that
machine was built 2026-08-17 at 19:21. But "it was fixed once" is not a
property anyone can rely on, and the failure mode is specific: a dialog is a
top-level window, so any part of it the app's stylesheet does not paint falls
back to the *system* palette. On a light Windows theme that is dark-on-white
inside a dark app, or worse, near-white text on white.

So this measures rather than assumes: each popup is rendered under each theme
and the actual pixels are checked — the background must be the theme's, and
the text on it must clear a contrast floor.
"""
import pytest

from PySide6.QtGui import QColor

from app.themes.theme_manager import THEME_KEYS, build_qss, get_palette

# WCAG AA for normal text is 4.5:1. Podbye's dim tiers sit deliberately below
# that on purpose (timestamps, faint metadata), so the floor here is the one
# for "you can read it at all" rather than the one for body copy.
_MIN_CONTRAST = 3.0


def _relative_luminance(colour: QColor) -> float:
    def channel(value: float) -> float:
        value /= 255.0
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
    return (0.2126 * channel(colour.red())
            + 0.7152 * channel(colour.green())
            + 0.0722 * channel(colour.blue()))


def _contrast(a: QColor, b: QColor) -> float:
    la, lb = _relative_luminance(a), _relative_luminance(b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


def _dialogs(qapp):
    """One of each popup the app can show, built the way the app builds them."""
    from app.screens.cleanup_dialog import CleanupConfirmDialog
    from app.widgets.close_dialog import CloseRunningDialog
    from app.widgets.progress import BusyDialog

    item = {"path": r"C:\Users\n\AppData\Local\Temp\thing", "name": "thing",
            "size": "12 MB", "size_bytes": 12 * 1024 ** 2, "risk": "Safe",
            "entity_type": "cache_folder", "category": "Cache & Temp",
            "file_count": 3, "ai_status": "none", "is_dir": True}
    return [
        ("cleanup", CleanupConfirmDialog([item])),
        ("close", CloseRunningDialog("A scan")),
        ("busy", BusyDialog("Opening session…")),
    ]


def _message_box(qapp):
    from PySide6.QtWidgets import QMessageBox
    box = QMessageBox()
    # A message box Podbye actually raises. It was the delete-session
    # confirmation until History stopped offering one; this file is about how
    # a QMessageBox is styled, so it only needs real text of a real length.
    box.setWindowTitle("Not found")
    box.setText("Full session data is unavailable.")
    box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    return box


def _close(widget, qapp):
    from PySide6.QtCore import QCoreApplication, QEvent
    widget.close()
    widget.setParent(None)
    widget.deleteLater()
    qapp.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    qapp.processEvents()


@pytest.fixture
def themed(qapp):
    """Apply a theme's stylesheet the way main.py does, then put it back."""
    original = qapp.styleSheet()
    applied = []

    def apply(theme_key):
        qapp.setStyleSheet(build_qss(theme_key))
        applied.append(theme_key)
        return get_palette(theme_key)

    yield apply
    qapp.setStyleSheet(original)


# ── the background is the app's, never the operating system's ─────

@pytest.mark.parametrize("theme_key", THEME_KEYS)
def test_a_dialog_paints_its_own_background(themed, qapp, theme_key):
    palette = themed(theme_key)
    expected = QColor(palette["bg"])
    for name, dialog in _dialogs(qapp):
        dialog.resize(560, 320)
        shot = dialog.grab().toImage()
        # Top-left, inside the frame: the one place no child widget covers.
        painted = QColor(shot.pixel(3, 3))
        assert _contrast(painted, expected) < 1.2, (
            f"{name} under {theme_key} painted {painted.name()} where the "
            f"theme is {expected.name()} — the system palette is showing "
            f"through")
        _close(dialog, qapp)


@pytest.mark.parametrize("theme_key", THEME_KEYS)
def test_a_message_box_paints_its_own_background(themed, qapp, theme_key):
    """The static QMessageBox helpers are used in nine places."""
    palette = themed(theme_key)
    expected = QColor(palette["bg"])
    box = _message_box(qapp)
    box.resize(420, 180)
    painted = QColor(box.grab().toImage().pixel(3, 3))
    assert _contrast(painted, expected) < 1.2, (
        f"a message box under {theme_key} painted {painted.name()}, "
        f"not {expected.name()}")
    _close(box, qapp)


# ── and the text on it can be read ────────────────────────────────

@pytest.mark.parametrize("theme_key", THEME_KEYS)
def test_dialog_text_has_contrast_against_it(themed, theme_key):
    palette = themed(theme_key)
    background = QColor(palette["bg"])
    for tier in ("text", "text_dim"):
        ratio = _contrast(QColor(palette[tier]), background)
        assert ratio >= _MIN_CONTRAST, (
            f"{theme_key}: {tier} on a dialog background is {ratio:.1f}:1")


@pytest.mark.parametrize("theme_key", THEME_KEYS)
def test_the_confirm_button_can_be_read(themed, theme_key):
    """The one button that deletes things has to be legible everywhere."""
    palette = themed(theme_key)
    ratio = _contrast(QColor(palette["bg"]), QColor(palette["accent"]))
    assert ratio >= _MIN_CONTRAST, (
        f"{theme_key}: the primary button is {ratio:.1f}:1 against the dialog")


# ── the buttons are not touching ──────────────────────────────────

def test_the_cleanup_buttons_are_not_merged(qapp, themed):
    themed("forest")
    dialog = _dialogs(qapp)[0][1]
    dialog.resize(620, 420)
    dialog.show()
    qapp.processEvents()

    cancel, confirm = dialog._btn_cancel, dialog._btn_confirm
    gap = confirm.geometry().left() - cancel.geometry().right()
    assert gap >= 6, f"only {gap}px between Cancel and the delete button"
    _close(dialog, qapp)


def test_every_dialog_spaces_its_button_row():
    """A layout's spacing cannot come from the stylesheet, so it is in code."""
    import inspect

    from app.screens import cleanup_dialog
    from app.widgets import ask_ai_dialog, close_dialog

    for module in (cleanup_dialog, ask_ai_dialog, close_dialog):
        source = inspect.getsource(module)
        if "btn_row = QHBoxLayout()" not in source:
            continue
        head = source.split("btn_row = QHBoxLayout()")[1][:400]
        assert "setSpacing" in head, (
            f"{module.__name__} builds a button row without setting spacing")
