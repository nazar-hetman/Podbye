"""One chip row, one behaviour, on both screens that have one.

Findings and Startups drew an identical row of risk chips and answered a click
differently: Findings chips were multi-select toggles that all start on, while
Startups chips were radio buttons with an "All" in front. Same look, same
labels, same place — so the same click filtered to one risk on one screen and
removed one risk on the other.

Both are now multi-select with an "All" that resets.

The two lists are no longer identical, and that is deliberate. Findings offers
Protected because there it is enforceable — the item cannot be selected and the
button is disabled. Startups does not: Podbye writes nothing to the registry,
so it modifies no startup entry and can never refuse to, and everything Windows
or a vendor owns is Review with a reason that says so. What is asserted here is
that each screen offers its own full set and behaves the same way about it.
"""
import os
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.models.risk import RISK_ORDER
from app.screens.startups import STARTUP_RISK_ORDER


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def startups(qapp):
    from app.screens.startups import StartupsScreen
    screen = StartupsScreen()
    # The chips live in the results view; the screen opens on its idle state.
    screen._show_results()
    return screen


@pytest.fixture
def findings(qapp):
    from app.screens.findings_dashboard import CategoryDetailView
    return CategoryDetailView()


# ── both screens offer the same chips ─────────────────────────────

def test_startups_offers_every_risk(startups):
    assert list(startups._risk_btns) == list(STARTUP_RISK_ORDER)


def test_startups_optional_filter_uses_the_same_startup_scoped_wording_as_rows(startups):
    """The backend keeps Optional; the UI consistently says what it means."""
    assert startups._risk_btns["Optional"].text() == "Optional at startup"


def test_findings_offers_every_risk(findings):
    assert list(findings._risk_btns) == list(RISK_ORDER)


def test_both_screens_have_an_all_chip(startups, findings):
    assert startups._all_risks_btn is not None
    assert findings._all_risks_btn is not None


# ── both start unfiltered ─────────────────────────────────────────

def test_startups_starts_showing_everything(startups):
    assert startups._risk_filter == set(STARTUP_RISK_ORDER)


def test_findings_starts_showing_everything(findings):
    assert all(b.isChecked() for b in findings._risk_btns.values())
    assert findings._all_risks_btn.isChecked()


# ── a chip click removes exactly one risk, on both ────────────────

def test_a_startups_chip_click_removes_only_that_risk(startups):
    startups._toggle_risk_filter("Safe")

    assert startups._risk_filter == {"Optional", "Review"}, (
        "clicking one chip filtered down to it — that is the old radio model")


def test_a_findings_chip_click_removes_only_that_risk(findings):
    findings._risk_btns["Safe"].setChecked(False)
    findings._apply_risk_filter()

    active = {r for r, b in findings._risk_btns.items() if b.isChecked()}
    assert active == {"Optional", "Review", "Protected"}


def test_startups_chips_combine(startups):
    """Two risks at once — impossible under the radio model."""
    startups._toggle_risk_filter("Safe")
    startups._toggle_risk_filter("Optional")

    assert startups._risk_filter == {"Review"}


# ── "All" resets, on both ─────────────────────────────────────────

def test_all_restores_everything_on_startups(startups):
    startups._toggle_risk_filter("Safe")
    startups._toggle_risk_filter("Review")
    startups._on_all_risks_clicked()

    assert startups._risk_filter == set(STARTUP_RISK_ORDER)


def test_all_restores_everything_on_findings(findings):
    findings._risk_btns["Safe"].setChecked(False)
    findings._apply_risk_filter()
    findings._on_all_risks_clicked()

    assert all(b.isChecked() for b in findings._risk_btns.values())


def test_all_tracks_the_other_chips_on_findings(findings):
    """It reads as active only while nothing is filtered out."""
    findings._risk_btns["Review"].setChecked(False)
    findings._apply_risk_filter()
    assert not findings._all_risks_btn.isChecked()

    findings._risk_btns["Review"].setChecked(True)
    findings._apply_risk_filter()
    assert findings._all_risks_btn.isChecked()


# ── the way back is never closed ──────────────────────────────────

def test_startups_refuses_to_hide_the_last_risk(startups):
    """An empty list with every chip off has no visible way back."""
    for risk in ("Safe", "Optional"):
        startups._toggle_risk_filter(risk)
    assert startups._risk_filter == {"Review"}

    # Clicking the last one standing is refused: an empty list with every
    # chip off has no visible way back.
    startups._toggle_risk_filter("Review")

    assert startups._risk_filter == {"Review"}


# ── chip labels are translatable ──────────────────────────────────

def test_findings_chip_labels_go_through_translation(qapp, monkeypatch):
    """These four were the only risk names left hardcoded in English."""
    from app.screens import findings_dashboard as fd
    # The module imported tr by name, so the module attribute is what runs.
    monkeypatch.setattr(fd, "tr", lambda s, **k: f"<{s}>")
    view = fd.CategoryDetailView()

    assert view._risk_btns["Safe"].text() == "<Safe>"
    assert view._all_risks_btn.text() == "<All>"
