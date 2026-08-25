"""One long path must not clip every other row in an inspection panel.

A filesystem path is a single unbreakable word. A word-wrapping QLabel reports
that whole word as its *minimum* width, and QScrollArea honours minimums — so
the Startups inspector demanded 1056 px inside a ~500 px sidebar and, with the
horizontal scrollbar switched off, silently cut every line at the panel edge:
the recommendation, the importance note and the AI reading all lost their
right-hand half.

What is pinned here is the property that failed, not the pixels: whatever a
panel does with a pathological value, its minimum width must stay inside the
sidebar it lives in.
"""
import pytest
from PySide6.QtCore import Qt

from app.models.startup_entry import StartupEntry
from app.screens.startups import StartupRightSidebar
from app.widgets.controls import ElidedLabel

# The sidebar as it renders on a 1920-wide window.
SIDEBAR_W = 500

NASTY_PATH = (r"C:\Users\Nazar\AppData\Local\Grammarly\DesktopIntegrations"
              r"\Grammarly.Desktop.Runtime\bin\Grammarly.Desktop.exe --autostart")


def _entry(path=NASTY_PATH, name="Grammarly"):
    return StartupEntry(
        name=name, command=path, path=path, publisher="Grammarly Inc.",
        source="run_hkcu", source_label="User startup registry", enabled=True,
        risk="Optional", risk_reason="Useful convenience startup",
        impact="Creative helper",
        recommendation="consider disabling this if you do not need it at sign-in",
    )


@pytest.fixture
def sidebar(qapp):
    side = StartupRightSidebar(ask_ai_cb=None)
    side.resize(SIDEBAR_W, 620)
    yield side
    side.close()
    side.deleteLater()
    qapp.processEvents()


def test_a_long_launch_path_does_not_widen_the_inspector(sidebar, qapp):
    sidebar.set_entry(_entry())
    qapp.processEvents()

    needed = sidebar.detail_widget.minimumSizeHint().width()

    assert needed <= SIDEBAR_W, (
        f"inspector demands {needed}px inside a {SIDEBAR_W}px sidebar; "
        f"the overflow is clipped, not scrolled")


def test_the_path_row_is_the_one_that_used_to_blow_it_up(sidebar, qapp):
    """Guard the specific label, so a future 'make it wrap again' is caught."""
    sidebar.set_entry(_entry())
    qapp.processEvents()

    path_lbl = sidebar.detail_widget._path_lbl

    assert isinstance(path_lbl, ElidedLabel)
    assert not path_lbl.wordWrap(), "a wrapping path label re-creates the bug"


def test_the_full_path_stays_available_in_the_tooltip(sidebar, qapp):
    """Elision is a display choice; the value must not be lost."""
    sidebar.set_entry(_entry())
    qapp.processEvents()

    assert sidebar.detail_widget._path_lbl.toolTip() == NASTY_PATH


def test_a_short_path_is_not_elided(sidebar, qapp):
    sidebar.set_entry(_entry(path=r"C:\app.exe"))
    qapp.processEvents()

    assert "…" not in sidebar.detail_widget._path_lbl.text()


# ── the shared widget ─────────────────────────────────────────────

# ── the same bug, in the Findings inspector ───────────────────────
#
# Found by looking at the running app: the reasoning header holds a caption
# and a state badge, and the badge's text grows with the answer's language
# ("Available · Simplified Chinese"). A plain QLabel refuses to shrink, so the
# row pushed the whole inspector past its viewport and the overflow was cut —
# 19 px on Ukrainian, 82 px on Simplified Chinese.

FINDINGS_SIDEBAR_W = 365   # what a 1456-wide window gives it

FINDING = {
    "path": "C:/Program Files/Microvirt", "name": "MEmu (Microvirt)",
    "risk": "Review", "entity_type": "installed_application",
    "category": "Applications", "size": "40.2 GB", "size_bytes": 1 << 35,
    "file_count": 479, "folder_count": 57,
}


@pytest.fixture
def findings_sidebar(qapp):
    """Factory that cleans up after itself.

    These tests build a full inspector per parameter, and an undestroyed one
    holds Qt window resources. Leaked across a file this size the process dies
    with an access violation part-way through the run rather than a failure —
    the same trap documented in test_analyze_hover.
    """
    made = []

    def _make(width=FINDINGS_SIDEBAR_W):
        from app.screens.findings_dashboard import RightSidebar
        side = RightSidebar(open_cb=lambda p: None, copy_cb=lambda p: None,
                            ask_ai_cb=lambda e: "")
        side.resize(width, 700)
        side.show()
        made.append(side)
        return side

    yield _make
    for side in made:
        side.close()
        side.deleteLater()
    qapp.processEvents()


def _inspector_min_width(findings_sidebar, language):
    from PySide6.QtWidgets import QApplication
    side = findings_sidebar()
    side.populate({**FINDING, "ai_status": "ready",
                   "ai_explanation": "x " * 200, "ai_language": language})
    QApplication.instance().processEvents()
    return side.detail_widget.minimumSizeHint().width(), side._scroll.viewport().width()


def test_the_answer_language_never_widens_the_findings_inspector(qapp, findings_sidebar):
    """The property, independent of which fonts the test process has.

    Absolute widths depend on the font — the app registers bundled ones at
    startup and a bare test process does not — so what is asserted is that the
    badge's text length does not reach the panel's width at all.
    """
    widths = {lang: _inspector_min_width(findings_sidebar, lang)[0]
              for lang in ("", "Ukrainian", "Simplified Chinese",
                           "Brazilian Portuguese")}

    spread = max(widths.values()) - min(widths.values())
    assert spread <= 4, (
        f"the answer's language changes how wide the inspector needs to be: "
        f"{widths}")


def test_the_findings_inspector_fits_its_sidebar_with_the_app_fonts(qapp, findings_sidebar):
    """The absolute check, with the app's own fonts loaded as main() does."""
    from app.fonts import load_fonts, FONT_UI
    from PySide6.QtGui import QFont

    previous = qapp.font()
    load_fonts()
    qapp.setFont(QFont(FONT_UI, 10))
    try:
        needed, viewport = _inspector_min_width(findings_sidebar, "Simplified Chinese")
    finally:
        # The QApplication is shared by the whole session; leaving a different
        # default font behind changes every later widget's metrics.
        qapp.setFont(previous)

    assert needed <= viewport, (
        f"inspector needs {needed}px in a {viewport}px viewport; "
        f"the overflow is clipped, not scrollable")


def test_the_state_badge_is_what_gives_way(findings_sidebar):
    """It is the one variable-length item; the caption is a fixed heading."""
    from app.widgets.controls import ElidedLabel
    side = findings_sidebar()
    assert isinstance(side.detail_widget._ai_state_badge, ElidedLabel)


def test_the_reasoning_caption_never_collapses(qapp, findings_sidebar):
    """It may shorten on a narrow panel; it may not disappear.

    An earlier attempt put the caption on an Ignored size policy so it would
    yield to the badge. Ignored widgets lose every pixel to a layout's stretch,
    so the heading vanished from the block entirely — the floor below is what
    keeps it on screen. Exact widths move with the font and the active
    stylesheet, so only the floor is asserted.
    """
    side = findings_sidebar()
    side.populate({**FINDING, "ai_status": "none"})
    qapp.processEvents()

    title = side.detail_widget._ai_title
    assert title.isVisible()
    assert title.width() >= 90, "the heading lost its space to the row's stretch"
    # Compared against the label's own full text rather than a hard-coded
    # word: the caption was renamed when the panel became one page, and what
    # this guards is that it is not elided away, whatever it says.
    assert title.text() and "…" not in title.text(), (
        f"the heading is unreadably short: {title.text()!r}")


def test_the_badge_shows_its_full_text_when_there_is_room(qapp, findings_sidebar):
    """Shrinking is for tight rows only — a wide panel must not elide."""
    side = findings_sidebar(width=900)
    side.populate({**FINDING, "ai_status": "ready",
                   "ai_explanation": "x " * 200, "ai_language": "Ukrainian"})
    qapp.processEvents()

    assert "…" not in side.detail_widget._ai_state_badge.text()


# ── readable truncation ───────────────────────────────────────────

@pytest.fixture
def compact():
    from app.screens.startups import StartupInspectorPanel
    return StartupInspectorPanel._compact_text


THREE_SENTENCES = (
    "Grammarly from Grammarly Inc. launches at sign-in to keep companion "
    "features available for creative or writing workflows. Disabling startup "
    "removes background convenience features until the app is opened manually. "
    "Disabling it is safe and reversible at any time."
)


def test_an_explanation_is_not_cut_mid_word(compact):
    """It used to end '...opened manually. Disabl...', which reads as corrupt."""
    out = compact(THREE_SENTENCES)
    assert not out.endswith("Disabl...")
    assert out.endswith("opened manually.")


def test_short_text_is_left_alone(compact):
    assert compact("Starts with Windows.") == "Starts with Windows."


def test_a_run_with_no_sentence_end_breaks_on_a_word(compact):
    text = "a very long run of words with no full stop anywhere in it " * 6
    out = compact(text)
    assert out.endswith("…")
    assert not out[:-1].endswith(" ")
    # the character before the ellipsis belongs to a whole word
    assert text.startswith(out[:-1].rstrip("…").rstrip())


def test_an_unbreakable_run_still_gets_shortened(compact):
    out = compact("a" * 300)
    assert len(out) <= 221 and out.endswith("…")


def test_whitespace_is_normalised(compact):
    assert compact("two\n\nlines   here") == "two lines here"


def test_the_full_text_stays_in_the_tooltip(qapp):
    """Shortening is a display choice; the answer must not be lost."""
    from app.screens.startups import StartupInspectorPanel
    panel = StartupInspectorPanel()
    try:
        entry = _entry()
        entry.ai_status = "ready"
        entry.ai_explanation = THREE_SENTENCES
        panel.set_entry(entry)

        assert panel._explanation_lbl.toolTip() == THREE_SENTENCES
        assert len(panel._explanation_lbl.text()) < len(THREE_SENTENCES)
    finally:
        panel.deleteLater()
        qapp.processEvents()


def test_elided_label_never_widens_its_container(qapp):
    """The whole point: it accepts the width the layout gives it.

    The label's own minimumSizeHint still reports the full text — QLabel
    computes it from the string. What breaks the chain is the Ignored size
    policy, which makes the layout disregard that hint, so the claim worth
    asserting is about the container, not the label.
    """
    from PySide6.QtWidgets import QVBoxLayout, QWidget

    monstrous = "C:/" + "very-long-segment/" * 40 + "file.dat"
    host = QWidget()
    QVBoxLayout(host).addWidget(ElidedLabel(monstrous))

    plain = QWidget()
    plain_lbl = ElidedLabel("")           # same widget, harmless text
    plain_lbl.setText("C:/short.exe")
    QVBoxLayout(plain).addWidget(plain_lbl)

    assert host.minimumSizeHint().width() == plain.minimumSizeHint().width(), (
        "text length is leaking into the container's minimum width")


def test_elided_label_shortens_to_fit_and_keeps_the_original(qapp):
    full = "C:/Users/Nazar/AppData/Local/Programs/Thing/bin/thing.exe"
    lbl = ElidedLabel(full, mode=Qt.ElideMiddle)
    lbl.resize(120, 20)
    lbl.show()

    assert lbl.text() != full and "…" in lbl.text()
    assert lbl.full_text() == full
    assert lbl.toolTip() == full


def test_setting_text_on_an_elided_label_still_elides(qapp):
    """Call sites use plain setText; they must not have to know it elides."""
    lbl = ElidedLabel(mode=Qt.ElideMiddle)
    lbl.resize(100, 20)
    lbl.show()
    lbl.setText("C:/Users/Nazar/AppData/Local/Programs/Thing/bin/thing.exe")

    assert "…" in lbl.text()
    assert lbl.toolTip() == "C:/Users/Nazar/AppData/Local/Programs/Thing/bin/thing.exe"
