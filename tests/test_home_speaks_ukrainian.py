"""The Home screen in Ukrainian, on a run the user stopped.

Audited against a real screenshot. Three kinds of fault were on it, and only
one of them was a translation problem:

* **English left on screen.** "82,277 items" and "Папка: All drives" — the
  session stores its display unit and its target in English, and Home printed
  both raw. "All drives" already had a Ukrainian value in uk.json; nothing was
  asking for it.
* **Wrong product term.** "Продовжити сканування" and "ПРОДОВЖИТИ ОСТАННІЙ
  ЗАПУСК" for a button that resumes the whole pipeline — walk, grouping and
  explanations — not just the filesystem walk. Three words (аналіз,
  сканування, запуск) were being used for one thing.
* **Wrong register.** "Перевірити" is an instruction; the legend it sits in is
  a count of items in a state. "Папка" for a set of drives is simply untrue.

The last one is the interesting case: the fix is a *different key*, not a
different value. The same "Review" key is on the compact risk badges in
Findings, where a status phrase would not fit.
"""
import re
import time

import pytest
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel, QPushButton

import app.screens.home as home
from app.fonts import FONT_UI, load_fonts
from app.i18n import set_language, tr
from app.themes.theme_manager import build_qss
from app.widgets.controls import ElidedLabel


NOW = time.time()

_STOPPED_SESSION = {
    "status": "stopped", "target": "All drives", "scan_mode": "smart",
    "scanned_count": 82277, "total_size": 34_300_000_000,
    "start_time": NOW - 900, "last_update": NOW - 120, "saved_at": NOW - 120,
    "display_count": 82277, "display_unit": "items",
    "reclaimable_bytes": 9 * 1024 ** 3,
    "risk_totals": {"Safe": 300, "Review": 740, "Protected": 45},
    "category_totals": {"Applications": {"count": 100, "size_bytes": 129 * 1024 ** 3}},
}


_LIFETIME = {
    "total_recovered_bytes": 1_100_000_000_000,
    "analyze_sessions": 118, "cleanup_sessions": 494,
    "total_cleanup_items": 13272, "total_scanned_bytes": 56_900_000_000_000,
}


@pytest.fixture
def ukrainian(qapp, monkeypatch):
    """Home in Ukrainian, dressed as main() dresses it.

    The fonts matter: every width assertion below is meaningless against the
    default font, which is not the one the app ships.
    """
    previous_font = qapp.font()
    load_fonts()
    qapp.setFont(QFont(FONT_UI, 10))
    qapp.setStyleSheet(build_qss("forest"))
    set_language("Ukrainian")
    monkeypatch.setattr(home, "load_session_summary",
                        lambda: dict(_STOPPED_SESSION))
    # The all-time banner reads summary.json, which conftest points at an
    # empty tmp dir — without this the card is skipped and every assertion
    # about its wording passes by finding nothing.
    monkeypatch.setattr(home, "load_summary", lambda: dict(_LIFETIME))
    yield qapp
    set_language("English")
    qapp.setFont(previous_font)


@pytest.fixture
def screen(ukrainian):
    made = []

    def build(width=1724, height=1000):
        s = home.HomeScreen()
        s.resize(width, height)
        s.show()
        for _ in range(10):
            ukrainian.processEvents()
        made.append(s)
        return s

    yield build
    for s in made:
        s.deleteLater()
    ukrainian.processEvents()


def _texts(s):
    out = [l.text() for l in s.findChildren(QLabel)
           if l.isVisibleTo(s) and l.text().strip()]
    out += [b.text() for b in s.findChildren(QPushButton)
            if b.isVisibleTo(s) and b.text().strip()]
    return out


def _all(s):
    return "\n".join(_texts(s))


# ── no English left on a Ukrainian screen ─────────────────────────

# Latin that legitimately survives translation: byte units, the product name,
# and AI itself — which stays AI in Ukrainian technical writing.
_ALLOWED_LATIN = {"AI", "TB", "GB", "MB", "KB", "PODBYE", "B"}


def test_no_english_words_are_left(screen):
    s = screen()
    leaked = set()
    for text in _texts(s):
        for word in re.findall(r"[A-Za-z]{2,}", text):
            if word.upper() not in _ALLOWED_LATIN:
                leaked.add(word)

    assert not leaked, f"untranslated on a Ukrainian screen: {sorted(leaked)}"


def test_the_unit_beside_the_count_is_translated(screen):
    """session_store writes display_unit in English; Home printed it raw."""
    s = screen()

    assert "елементів" in _all(s)
    assert "items" not in _all(s)


def test_the_target_is_translated_when_it_is_a_phrase(screen):
    """"All drives" is a choice, not a path. uk.json had "Усі диски" all
    along — Home was never asking for it."""
    s = screen()

    assert "Усі диски" in _all(s)
    assert "All drives" not in _all(s)


def test_a_real_path_is_not_mangled_by_that(ukrainian, monkeypatch):
    """tr() falls back to its key, which is what makes translating the target
    safe — but a path must come through exactly as stored."""
    session = dict(_STOPPED_SESSION, target="D:/Games/Steam")
    monkeypatch.setattr(home, "load_session_summary", lambda: dict(session))
    monkeypatch.setattr(home, "load_summary", lambda: dict(_LIFETIME))
    s = home.HomeScreen()
    s.resize(1724, 1000)
    s.show()
    for _ in range(10):
        ukrainian.processEvents()
    try:
        assert "D:/Games/Steam" in _all(s)
    finally:
        s.deleteLater()
        ukrainian.processEvents()


# ── one word per concept ──────────────────────────────────────────

def test_the_all_time_heading_reads_naturally(screen):
    """"ЗАГАЛЬНИЙ ВНЕСОК" is what a donation page says."""
    s = screen()

    assert "ЗА ВЕСЬ ЧАС" in _all(s)
    assert "ЗАГАЛЬНИЙ ВНЕСОК" not in _all(s)


def test_the_heading_does_not_repeat_itself(screen):
    """The hero figure's caption used to be "звільнено за весь час" under a
    heading that now says exactly that."""
    s = screen()

    assert _texts(s).count("звільнено") == 1
    assert "звільнено за весь час" not in _all(s)


def test_resuming_is_resuming_the_analysis(screen):
    """The button restarts the pipeline — walk, grouping and explanations —
    so naming it after the walk describes the smallest part of what it does.
    "останній запуск" named nothing at all."""
    s = screen()
    everything = _all(s)

    assert "Продовжити аналіз" in everything
    assert "ПРОДОВЖИТИ АНАЛІЗ" in everything
    assert "Продовжити сканування" not in everything
    assert "ОСТАННІЙ ЗАПУСК" not in everything


def test_starting_is_starting_an_analysis(screen):
    """Same action in the other direction, so the pair reads as one verb."""
    s = screen()

    assert "Новий аналіз" in _all(s)
    assert "Нове сканування" not in _all(s)


def test_the_ai_card_names_the_feature(screen):
    """"ОБРОБКА AI" describes a machine working. The card counts finished
    explanations."""
    s = screen()

    assert "AI-АНАЛІЗ" in _all(s)
    assert "ОБРОБКА AI" not in _all(s)


def test_nothing_is_abbreviated_that_fits_in_full(screen):
    s = screen()

    assert "елементів прибрано з диска" in _all(s)
    assert "елем." not in _all(s)


# ── the right register ────────────────────────────────────────────

def test_the_scope_line_does_not_call_drives_a_folder(screen):
    s = screen()

    assert "Область: Усі диски" in _all(s)
    assert "Папка:" not in _all(s)


def test_the_legend_names_a_state_not_an_instruction(screen):
    """"Перевірити" tells the user to do something. The legend is counting
    items that are in a condition."""
    s = screen()

    assert any("Потребують перевірки" in t for t in _texts(s))


def test_the_compact_risk_badges_keep_the_short_word(ukrainian):
    """Why this needed a new key rather than a new value for "Review".

    The badges in Findings are sized for a word. Giving the shared key the
    legend's phrase would have put "Потребують перевірки" into every one of
    them, and they are not built for it.
    """
    assert tr("Review") == "Перевірити"
    assert tr("Needs review") == "Потребують перевірки"


def test_the_category_count_is_grammatical_for_one(screen):
    """tr() has no plural forms, so "у {n} категоріях" was wrong for exactly
    the case this screen shows most often. A label-and-count form is correct
    for every number."""
    s = screen()

    assert "категорій: 1" in _all(s)
    assert "у 1 категоріях" not in _all(s)


# ── the footer was making a claim it could not make ───────────────

def test_a_stopped_run_is_not_reported_as_finished(qapp):
    """The footer reports what the *app* is doing, and "Complete" is its
    idle-with-data label. A stopped run is idle and has data, so it fell
    through to it — and the word was read as a verdict on the analysis, two
    lines below a Home badge saying Paused.
    """
    from app.main import PodbyeWindow

    class _State:
        is_analysis_active = False
        current_phase = "stopped"
        total_count = 82277
        entity_count = 0

    window = PodbyeWindow.__new__(PodbyeWindow)   # no real UI needed
    window._ai_explainer = None
    window._scan_state = _State()

    assert window._shell_status_text() == tr("Paused")


def test_the_footer_and_the_badge_use_the_same_word(ukrainian):
    """Whatever the footer says, it must not be a third word for the state
    Home already names twice."""
    assert tr("Paused") == "Призупинено"


def test_a_finished_run_still_reports_complete(qapp):
    """Only the stopped case moved."""
    from app.main import PodbyeWindow

    class _State:
        is_analysis_active = False
        current_phase = "complete"
        total_count = 82277
        entity_count = 40

    window = PodbyeWindow.__new__(PodbyeWindow)
    window._ai_explainer = None
    window._scan_state = _State()

    assert window._shell_status_text() == tr("Complete")


def test_an_untouched_app_still_reports_ready(qapp):
    from app.main import PodbyeWindow

    class _State:
        is_analysis_active = False
        current_phase = "idle"
        total_count = 0
        entity_count = 0

    window = PodbyeWindow.__new__(PodbyeWindow)
    window._ai_explainer = None
    window._scan_state = _State()

    assert window._shell_status_text() == tr("Ready")


# ── and all of it fits ────────────────────────────────────────────

def _fit_faults(root):
    out = []
    for lbl in root.findChildren(QLabel):
        if (not lbl.isVisibleTo(root) or lbl.width() < 4
                or not lbl.text().strip() or isinstance(lbl, ElidedLabel)):
            continue
        if lbl.wordWrap():
            want = lbl.heightForWidth(lbl.width())
            if want > lbl.height() + 1:
                out.append(f"cut below: {lbl.text()[:36]!r} has {lbl.height()} wants {want}")
        elif lbl.sizeHint().width() > lbl.width() + 1:
            out.append(f"cut sideways: {lbl.text()[:36]!r} has {lbl.width()} "
                       f"wants {lbl.sizeHint().width()}")
    for btn in root.findChildren(QPushButton):
        if btn.isVisibleTo(root) and btn.text() and btn.sizeHint().width() > btn.width() + 1:
            out.append(f"button cut: {btn.text()[:36]!r} has {btn.width()} "
                       f"wants {btn.sizeHint().width()}")
    return out


@pytest.mark.parametrize("width,height", [(1724, 1000), (884, 620)])
def test_the_ukrainian_home_fits_its_window(screen, width, height):
    """884x620 is the content area beside the 196px sidebar at the 1100x700
    minimum main.py enforces."""
    s = screen(width, height)

    assert _fit_faults(s) == []


def test_the_legend_clears_its_card_with_seven_figure_counts(ukrainian, monkeypatch):
    """"Потребують перевірки" is 12 characters longer than "Перевірити", and
    the count sits after it. A 2TB scan is where that runs out of room, not
    the 740 the audit happened to be looking at.
    """
    session = dict(_STOPPED_SESSION,
                   risk_totals={"Safe": 123456, "Review": 1234567, "Protected": 98765})
    monkeypatch.setattr(home, "load_session_summary", lambda: dict(session))
    monkeypatch.setattr(home, "load_summary", lambda: dict(_LIFETIME))
    s = home.HomeScreen()
    s.resize(884, 620)
    s.show()
    for _ in range(10):
        ukrainian.processEvents()
    try:
        for lbl in s.findChildren(QLabel):
            if not lbl.isVisibleTo(s) or "перевірки" not in lbl.text():
                continue
            card = lbl.parentWidget()
            right = lbl.mapTo(s, lbl.rect().topLeft()).x() + lbl.width()
            card_right = card.mapTo(s, card.rect().topLeft()).x() + card.width()
            assert right <= card_right, f"{lbl.text()!r} overruns its card"
    finally:
        s.deleteLater()
        ukrainian.processEvents()
