"""A section title and its caption share a row, and both used to lose text.

Reported after Spanish, German and Polish were added. At the smallest supported
window the Findings list header read

    APLICACIONES Y CARPETAS   ->   painted 144px of the 193px it needs
    APLIKACJE I FOLDERY       ->   painted 132px of 155px

with no ellipsis and no scrollbar — a word cut mid-glyph.

The layout assumption is ``[tactical title] [muted caption] [stretch]``: fine
while the two fit. When the row is narrower than both, Qt spreads the shortfall
over every item that can shrink, and a plain QLabel shrinks by *clipping*. So
the title and the caption lost text together, and the title is the half that
says what you are looking at.

English hid it. "APPS & FOLDERS" is 119px in a 302px pane; the Spanish string
is 193px. Three sites shared the pattern — the Findings list header and both
Startups inspector headers — and the Findings inspector had already met it and
fixed it locally with an ElidedLabel. ``meta_caption()`` makes that the shared
answer: the caption asks for an ellipsis and no more, so it absorbs the
shortfall and the title keeps its text.

Two blind spots let this through, and both are closed here:

* ``test_text_fits_its_space.py`` runs its hostile Findings case in English
  only — the multi-language coverage there is on Startups.
* Its narrow width is 1100, which is the **window** minimum. The sidebar is a
  fixed 196px (``app/widgets/sidebar.py``), so a screen at the 1100px minimum
  is 884px wide. Every test below uses 884.
"""
import time

import pytest
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel, QPushButton

from app.fonts import FONT_UI, load_fonts
from app.i18n import available_languages, set_language
from app.themes.theme_manager import build_qss
from app.widgets.controls import ElidedLabel
from app.widgets.pills import Badge

# The content area beside the 196px sidebar at main.py's 1100x700 minimum.
NARROW = 884

LONG_NAME = ("Adobe Photoshop 2024 Creative Cloud Edition with Camera Raw and "
             "Neural Filters Extended Pack")
LONG_PATH = ("C:/Users/ExampleUser/AppData/Local/Programs/SomeVendor/Application/"
             "resources/app.asar.unpacked/node_modules/@scope/package-name/dist")
LONG_PROSE = ("This program registers a background helper that starts with the "
              "session and keeps features available to other applications. ") * 4


@pytest.fixture
def dressed(qapp):
    previous = qapp.font()
    load_fonts()
    qapp.setFont(QFont(FONT_UI, 10))
    qapp.setStyleSheet(build_qss("forest"))
    yield qapp
    set_language("English")
    qapp.setFont(previous)


def _faults(root):
    """Text that neither wraps nor elides and is narrower than it needs."""
    out = []
    for w in (root.findChildren(QLabel) + root.findChildren(QPushButton)
              + root.findChildren(Badge)):
        if not w.isVisibleTo(root) or not w.text().strip() or w.width() < 4:
            continue
        if isinstance(w, ElidedLabel):
            continue
        if isinstance(w, QLabel) and w.wordWrap():
            need = w.heightForWidth(w.width())
            if need > w.height() + 1:
                out.append(f"cut below ({w.height()} of {need}px): {w.text()[:40]!r}")
            continue
        if w.sizeHint().width() > w.width() + 1:
            out.append(f"cut sideways ({w.width()} of {w.sizeHint().width()}px): "
                       f"{w.text()[:40]!r}")
    return out


def _findings(dressed, width=NARROW):
    import app.screens.findings_dashboard as fd

    entities = [{
        "path": f"{LONG_PATH}/{i}", "name": LONG_NAME,
        "entity_type": "application", "entity_type_label": "Installed application",
        "size_bytes": 1234567890123 + i, "size": "1.1 TB",
        "file_count": 1234567, "folder_count": 98765,
        "risk": ("Safe", "Review", "Protected")[i % 3], "category": "Applications",
        "actionability": "recycle", "children_sample": [],
        "ai_status": ("ready", "analyzing", "failed")[i % 3],
        "ai_explanation": LONG_PROSE} for i in range(6)]
    view = fd.CategoryDetailView()
    view._app_index_cache = {}
    view.set_category("Applications", entities)
    view.resize(width, 620)
    view.show()
    for _ in range(8):
        dressed.processEvents()
    view.select_by_path(entities[0]["path"])
    for _ in range(8):
        dressed.processEvents()
    return view


def _startups(dressed, width=NARROW):
    import app.screens.startups as st
    from app.models.startup_entry import StartupEntry

    rows = []
    for i in range(6):
        entry = StartupEntry(
            name=LONG_NAME, command=LONG_PATH, path=LONG_PATH,
            publisher="A Very Long Publisher Name Corporation International Ltd",
            source="run_hkcu",
            source_label="Scheduled task (logon, per-user, elevated)",
            enabled=bool(i % 2), risk=("Optional", "Review", "Protected")[i % 3],
            risk_reason=LONG_PROSE, impact="Remote access service")
        entry.target_modified = time.time() - 5 * 365 * 86400
        entry.ai_status = ("ready", "analyzing", "failed")[i % 3]
        entry.ai_explanation = LONG_PROSE
        rows.append(entry)
    # showEvent() re-reads the machine and would replace these rows with the
    # real ones, clearing the selection — so the panel would render its empty
    # state and this would audit a screen with nothing on it. Caught when the
    # same hazard broke test_text_fits_its_space intermittently.
    import app.services.startup_detector as detector
    detector.detect_startup_entries = lambda: list(rows)

    screen = st.StartupsScreen()
    screen._entries = rows
    screen._filtered = list(rows)
    screen.resize(width, 620)
    screen.show()
    screen._show_results()
    for _ in range(8):
        dressed.processEvents()
    screen._select_entry(rows[0].key)
    for _ in range(8):
        dressed.processEvents()
    assert screen._selected_key, "the inspector fell back to its empty state"
    return screen


# ── the reported symptom, in every shipped language ───────────────

@pytest.mark.parametrize("language", available_languages())
def test_the_findings_list_header_keeps_its_title(dressed, language):
    set_language(language)
    view = _findings(dressed)
    try:
        title = view._list_title_lbl
        assert title.sizeHint().width() <= title.width() + 1, (
            f"{language}: {title.text()!r} painted {title.width()}px of "
            f"{title.sizeHint().width()}px")
    finally:
        view.deleteLater()
        dressed.processEvents()


@pytest.mark.parametrize("language", available_languages())
def test_the_startups_inspector_header_keeps_its_title(dressed, language):
    set_language(language)
    screen = _startups(dressed)
    try:
        panel = screen._right_sidebar.detail_widget
        title = panel._title_lbl
        assert title.sizeHint().width() <= title.width() + 1, (
            f"{language}: {title.text()!r} painted {title.width()}px of "
            f"{title.sizeHint().width()}px")
    finally:
        screen.deleteLater()
        dressed.processEvents()


# ── the coverage gap that let it through ──────────────────────────

@pytest.mark.parametrize("language", available_languages())
def test_findings_survives_hostile_content_in_every_language(dressed, language):
    """The existing hostile Findings case runs in English only."""
    set_language(language)
    view = _findings(dressed)
    try:
        assert _faults(view) == []
    finally:
        view.deleteLater()
        dressed.processEvents()


@pytest.mark.parametrize("language", available_languages())
def test_startups_survives_hostile_content_at_the_real_narrow_width(dressed, language):
    """The existing case uses 1100 — the *window* minimum. A screen there is
    884 wide, because the sidebar takes a fixed 196."""
    set_language(language)
    screen = _startups(dressed)
    try:
        assert _faults(screen) == []
    finally:
        screen.deleteLater()
        dressed.processEvents()


# ── the mechanism, so the fix cannot be undone by accident ────────

@pytest.mark.parametrize("language", available_languages())
def test_the_caption_is_still_shown_when_there_is_room(dressed, language):
    """The other half of the trade. Making the caption shrinkable is right;
    letting it disappear is not — an Ignored size policy asks for nothing, so
    the row's trailing stretch took every pixel and the count vanished from a
    header with 1500px to spare."""
    set_language(language)
    view = _findings(dressed, width=1500)
    try:
        caption = view._list_count_lbl
        assert caption.width() > 0, "the caption rendered at zero width"
        assert caption.text().strip().strip("…"), "elided away with room to spare"
    finally:
        view.deleteLater()
        dressed.processEvents()


def test_a_meta_caption_can_give_way(dressed):
    """The whole point: it must ask for almost nothing so the title wins."""
    from app.widgets.panels import meta_caption

    caption = meta_caption("// 1,234 visible")

    assert isinstance(caption, ElidedLabel)
    assert caption.minimumSizeHint().width() < 40, (
        "a caption that cannot shrink takes the title's pixels")


def test_the_header_rows_all_use_it(dressed):
    """Three sites shared the pattern. A fourth added later should too."""
    set_language("Spanish")
    view = _findings(dressed)
    screen = _startups(dressed)
    try:
        panel = screen._right_sidebar.detail_widget
        assert isinstance(view._list_count_lbl, ElidedLabel)
        assert isinstance(panel._selection_lbl, ElidedLabel)
    finally:
        view.deleteLater()
        screen.deleteLater()
        dressed.processEvents()


# ── and it holds under Windows font scaling ───────────────────────

@pytest.mark.parametrize("points", [12, 14])
def test_the_headers_hold_at_windows_font_scaling(dressed, points):
    """125% and 150% scaling raise every metric while the pane stays put."""
    dressed.setFont(QFont(FONT_UI, points))
    set_language("Spanish")
    view = _findings(dressed)
    try:
        title = view._list_title_lbl
        assert title.sizeHint().width() <= title.width() + 1
    finally:
        view.deleteLater()
        dressed.processEvents()
