"""What the Analyze screen says once the run is over.

Four things went on claiming the pipeline was mid-flight after it had finished,
which is the one moment the screen has to be unambiguous — the user is deciding
whether the numbers can be acted on.
"""
import pytest
from PySide6.QtGui import QColor

from app.screens.analyze import AnalyzeScreen, _chip_styles, _rgba
from app.themes import theme_manager as tm
from app.i18n import get_language, set_language, tr


@pytest.fixture
def screen(qapp):
    s = AnalyzeScreen()
    s.resize(1500, 900)
    s.show()
    qapp.processEvents()
    yield s
    s.deleteLater()


# ── "partial" has to follow the run ───────────────────────────────

def test_the_panel_is_partial_while_the_pipeline_works(screen):
    screen._pipeline_state = "scanning_filesystem"
    screen._update_findings_title()
    assert screen._pf_title.text() == "PARTIAL FINDINGS"


def test_the_panel_drops_partial_once_complete(screen):
    """It used to warn that finished results were incomplete."""
    screen._pipeline_state = "complete"
    screen._update_findings_title()
    assert screen._pf_title.text() == "FINDINGS"


@pytest.mark.parametrize("state", ["stopped", "idle", "ai_classifying"])
def test_a_run_that_did_not_finish_stays_partial(screen, state):
    """A stopped run really is partial — that warning must survive."""
    screen._pipeline_state = state
    screen._update_findings_title()
    assert screen._pf_title.text() == "PARTIAL FINDINGS"


# ── skipped is not zero percent ───────────────────────────────────

def test_skipped_ai_does_not_report_zero_percent(screen):
    """"0%" next to a chip reading "skipped" looks like a job that stalled."""
    screen._ai_prog_lbl.setText("0%")
    screen._mark_ai_skipped()
    assert screen._ai_prog_lbl.text() != "0%"
    assert screen._ai_prog_lbl.text() == "\u2014"


def test_ukrainian_analyze_uses_grouped_results_and_explicit_ai_non_run(qapp):
    """Analyze should describe user-visible groups, not internal entities."""
    previous = get_language()
    set_language("Ukrainian")
    try:
        screen = AnalyzeScreen()
        screen.resize(1100, 700)
        screen.show()
        qapp.processEvents()

        assert [chip._label for chip in screen._chips] == [
            "Пошук шляхів",
            "Сканування й категоризація",
            "Групування результатів",
            "AI-класифікація",
        ]
        assert tr("{count:,} grouped results", count=1311) == (
            "згрупованих результатів: 1,311")
        assert tr("Not run") == "Не запускалося"
        assert tr("// stdout") == "// stdout"
    finally:
        screen.deleteLater()
        set_language(previous)


# ── the sentinel that reached the screen ──────────────────────────

def test_the_final_progress_tick_leaves_no_path_text(screen):
    """ScanWorker's last progress emit carries the path being scanned. It used
    to pass the literal string "done", which this label printed verbatim under
    the elapsed clock."""
    screen._on_progress(1234, "")
    assert screen._current_path_lbl.text() == ""


def test_the_scanner_no_longer_emits_a_word_where_a_path_goes():
    import inspect
    from app.services import scanner
    src = inspect.getsource(scanner)
    assert 'progress.emit(self._scanned, "done")' not in src


# ── a finished stage is a status, not a button ────────────────────

@pytest.mark.parametrize("theme", ["forest", "amber", "mono", "paper"])
def test_a_completed_stage_card_is_not_filled(theme):
    """Paper was the worst of them: safe_soft is a solid tan panel, so a row of
    finished stages read as a row of buttons."""
    tm._current_theme_key = theme
    done = _chip_styles()["done"]
    assert "background: transparent" in done, f"{theme}: completed stage is filled"


@pytest.mark.parametrize("theme", ["forest", "amber", "mono", "paper"])
def test_the_active_stage_keeps_its_fill(theme):
    """The one stage actually working should still draw the eye."""
    tm._current_theme_key = theme
    assert "background: transparent" not in _chip_styles()["active"]


# ── the colour bug behind it ──────────────────────────────────────

def test_hex_alpha_suffixes_are_gone_from_chip_styles():
    """"#7aa88a" + "70" is an eight-digit hex, and Qt reads those as #AARRGGBB.
    The channels rotate: the intended faded green rgb(122,168,138) came out as
    rgb(168,138,112), a tan belonging to no palette in the app."""
    rotated = QColor("#7aa88a70")
    assert (rotated.red(), rotated.green(), rotated.blue()) == (168, 138, 112), (
        "Qt changed how it parses 8-digit hex; revisit this")

    for theme in ("forest", "amber", "mono", "paper"):
        tm._current_theme_key = theme
        for state, qss in _chip_styles().items():
            assert "}7" not in qss and "}8" not in qss, f"{theme}/{state}: hex alpha"


def test_rgba_helper_preserves_channel_order():
    assert _rgba("#7aa88a", 0.45) == "rgba(122, 168, 138, 0.45)"


def test_rgba_helper_passes_through_anything_unexpected():
    assert _rgba("transparent", 0.5) == "transparent"
