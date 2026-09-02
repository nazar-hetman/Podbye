"""A user with no local model is given a next step, not just a diagnosis.

Two dead ends, both reached by the same person: someone who has never run a
local model and wants Podbye to explain something.

**"no local AI runtime on this machine"** was accurate and offered nothing to
click. The sentence under it — "Install Ollama or LM Studio, then press Test" —
names two products to a reader who by definition does not know where either
lives, and Podbye contained no link to either: neither ``ollama.com`` nor
``lmstudio.ai`` appeared anywhere in the tree. Every *other* failure state had
a next action (start the runtime, load a model, check the address); the one
state a beginner actually lands in did not.

**"Select an AI model in Settings to use Ask AI."** names a destination in an
app whose Settings has six sections, and does not go there.

The rule both fixes follow is the one already written beside the About links:
a URL is handed to the system browser and that is the end of Podbye's
involvement. Nothing here downloads, installs, checks a version or opens a
socket — test_offline_guarantee still holds with these buttons on screen,
which is asserted below rather than assumed.
"""
import pytest
from PySide6.QtCore import QObject, Signal

from app.services import ollama_client as oc
from app.version import LM_STUDIO_URL, OLLAMA_URL

HINT_INSTALL = ("Podbye needs one of these installed to explain anything. The "
                "buttons open their official sites in your browser \u2014 install "
                "either one, then press Test. Podbye only ever talks to your own "
                "machine or LAN.")
HINT_PULL = ("The server is running but has no models yet. Open Terminal or "
             "PowerShell and run this command there \u2014 it is not something "
             "to type into Podbye:  ollama pull llama3.2:3b")


def _dispose(widget, qapp):
    """Take a widget down now, not whenever the collector gets to it.

    deleteLater() only *posts* a DeferredDelete, and processEvents() outside a
    running event loop does not deliver it — so the C++ tree survives until
    Python collects the wrapper, and PySide then destroys it from inside the
    garbage collector. That is an access violation with no traceback, landing
    on whatever unrelated test the GC happened to run under: this suite died
    in ast.parse inside a locale test, ~1500 tests away from the cause.

    tests/test_switching_language_is_not_a_stall.py documents the same failure
    from the same cause.
    """
    from PySide6.QtCore import QCoreApplication, QEvent

    widget.close()
    widget.deleteLater()
    qapp.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    qapp.processEvents()


@pytest.fixture
def screen(qapp):
    from app.screens.settings import SettingsScreen
    s = SettingsScreen()
    yield s
    _dispose(s, qapp)


def _apply(screen, status, backend="", models=(), runtime=""):
    screen._on_connection_result(status, backend, list(models), runtime)


# ── the state that had no next step ───────────────────────────────

def test_nothing_installed_offers_both_runtimes(screen):
    _apply(screen, oc.STATUS_NOT_INSTALLED)

    shown = [b.text() for b in screen._runtime_links if not b.isHidden()]
    assert shown == ["Get Ollama", "Get LM Studio"]


def test_the_buttons_say_where_they_send_you(screen):
    """Same courtesy as the About links: the address is visible before it is
    opened, so nobody has to trust an unlabelled button."""
    tips = {b.toolTip() for b in screen._runtime_links}

    assert tips == {OLLAMA_URL, LM_STUDIO_URL}


def test_they_are_the_official_sites():
    """A link Podbye offers has to be one the user would have found anyway —
    never a mirror, a shortened link or a direct binary."""
    assert OLLAMA_URL.startswith("https://ollama.com/")
    assert LM_STUDIO_URL.startswith("https://lmstudio.ai")


def test_clicking_one_opens_a_browser_and_makes_no_request(screen, monkeypatch):
    """QDesktopServices, not urllib. The whole promise of the app is that it
    does not make requests, and an install link is exactly where that would
    quietly stop being true."""
    import PySide6.QtGui as qtgui

    opened = []
    monkeypatch.setattr(qtgui.QDesktopServices, "openUrl",
                        staticmethod(lambda url: opened.append(url.toString())))

    def _no_sockets(*a, **k):
        raise AssertionError("opened a socket to fetch an install page")

    monkeypatch.setattr(oc, "_get_json", _no_sockets)

    _apply(screen, oc.STATUS_NOT_INSTALLED)
    screen._runtime_links[0].click()

    assert opened == [OLLAMA_URL]


@pytest.mark.parametrize("status,backend,models,runtime", [
    (oc.STATUS_NOT_RUNNING, "", (), r"C:\ollama.exe"),
    (oc.STATUS_ONLINE, oc.BACKEND_OLLAMA, ({"name": "llama3.2:3b", "size": 1},), ""),
    (oc.STATUS_NO_MODELS, oc.BACKEND_OLLAMA, (), ""),
    (oc.STATUS_UNREACHABLE, "", (), ""),
    (oc.STATUS_REFUSED, "", (), ""),
])
def test_no_other_state_tells_you_to_install_one(screen, status, backend,
                                                 models, runtime):
    """Offering "Get Ollama" beside a running server, or beside one that is
    merely stopped, tells the user to install what they already have."""
    _apply(screen, status, backend=backend, models=models, runtime=runtime)

    assert all(b.isHidden() for b in screen._runtime_links), status


def test_the_hint_points_at_the_buttons(screen):
    _text, _colour, hint = screen._connection_message(oc.STATUS_NOT_INSTALLED, "", 0)

    assert "buttons open their official sites" in hint
    # And the guarantee stays attached to the offer.
    assert "your own machine or LAN" in hint


# ── a command is a command, not a field ───────────────────────────

def test_the_pull_example_is_marked_as_a_terminal_command(screen):
    """It sits under the Endpoint input and read like a value to paste into
    it. The reader who needs the hint is the one who cannot tell."""
    _text, _colour, hint = screen._connection_message(
        oc.STATUS_NO_MODELS, oc.BACKEND_OLLAMA, 0)

    assert "Terminal or PowerShell" in hint
    assert "not something to type into Podbye" in hint
    assert "ollama pull llama3.2:3b" in hint


def test_the_lm_studio_route_is_still_its_own(screen):
    """LM Studio loads a model from its own window; telling that user to run
    ollama pull is noise. The command hint must not have swallowed it."""
    _text, _colour, hint = screen._connection_message(
        oc.STATUS_NO_MODELS, oc.BACKEND_OPENAI, 0)

    assert "Developer tab" in hint
    assert "ollama pull" not in hint


# ── Ask AI goes where it points ───────────────────────────────────

class _NoModelExplainer(QObject):
    """Refuses the way the real one does when ai_model is unset.

    A QObject with the real signal, because the dialog connects to
    finding_updated before it asks — so a fast cached answer is not missed.
    """

    finding_updated = Signal(object)

    def __init__(self):
        super().__init__()
        self._session_id = ""

    def explain_item(self, item, force_refresh=False, facts=None):
        return "no-model"


class _Item:
    name = "node_modules"
    path = "C:/p/node_modules"
    ai_status = ""
    ai_explanation = ""


@pytest.fixture
def dialog(qapp):
    from app.widgets.ask_ai_dialog import AskAIDialog

    made = []

    def build(explainer=None):
        d = AskAIDialog(_Item(), explainer or _NoModelExplainer())
        made.append(d)
        return d

    yield build
    for d in made:
        _dispose(d, qapp)


def test_the_refusal_offers_a_way_there(dialog):
    d = dialog()

    assert "Select an AI model in Settings" in d._status.text()
    assert not d._btn_ai_settings.isHidden()


def test_the_button_is_absent_until_it_is_needed(dialog):
    """A configured install must not grow a stray "Open AI Settings"."""

    class _Working(_NoModelExplainer):
        def explain_item(self, item, force_refresh=False, facts=None):
            return ""

    d = dialog(_Working())

    assert d._btn_ai_settings.isHidden()


def test_it_asks_rather_than_navigating_itself(dialog, qapp):
    """The dialog does not know where Settings is, and must not learn: the
    shell owns navigation."""
    d = dialog()
    asked = []
    d.ai_settings_requested.connect(lambda: asked.append(1))

    d._btn_ai_settings.click()
    qapp.processEvents()

    assert asked == [1]


def test_it_closes_itself_first(dialog, qapp):
    """It is modal. Opening Settings behind a window the user cannot see past
    would look like nothing happened."""
    d = dialog()
    seen = []
    d.ai_settings_requested.connect(lambda: seen.append(d.isVisible()))

    d._btn_ai_settings.click()
    qapp.processEvents()

    assert seen == [False]


def test_ask_again_offers_it_too(dialog, qapp):
    """The second refusal is the same refusal."""
    d = dialog()
    d._btn_ai_settings.setVisible(False)

    d._on_ask_again()
    qapp.processEvents()

    assert not d._btn_ai_settings.isHidden()


# ── and the request reaches Settings, on the AI section ───────────

def test_settings_can_be_opened_on_one_section(qapp):
    from app.screens.settings import SettingsScreen, _SECTIONS

    s = SettingsScreen()
    try:
        s.open_section("ai")
        assert s._stack.currentIndex() == [x[0] for x in _SECTIONS].index("ai")
    finally:
        _dispose(s, qapp)


def test_an_unknown_section_is_ignored_not_raised(qapp):
    """The caller is a button. A typo must not take the window down."""
    from app.screens.settings import SettingsScreen

    s = SettingsScreen()
    try:
        s.open_section("nope")          # must not raise
    finally:
        _dispose(s, qapp)


def test_the_dashboard_forwards_the_request():
    """CategoryDetailView raises it, the dashboard re-exports it, main.py
    listens. A break anywhere in that chain is a button that does nothing."""
    from app.screens.findings_dashboard import CategoryDetailView, FindingsDashboard

    assert hasattr(CategoryDetailView, "ai_settings_requested")
    assert hasattr(FindingsDashboard, "ai_settings_requested")


def test_the_shell_wires_it_to_the_ai_section():
    """Asserted on the source: constructing a whole PodbyeWindow to check one
    connect() is the heaviest fixture in the suite, and this is the fact that
    matters — the handler exists and asks for "ai"."""
    import inspect

    from app.main import PodbyeWindow

    handler = inspect.getsource(PodbyeWindow._navigate_to_ai_settings)
    assert 'self._navigate("Settings")' in handler
    assert 'open_section("ai")' in handler

    wiring = inspect.getsource(PodbyeWindow._build_ui)
    assert "ai_settings_requested.connect" in wiring


# ── every new string is translatable ──────────────────────────────

@pytest.mark.parametrize("language", ["Ukrainian", "German", "Spanish",
                                      "Polish", "French"])
def test_the_new_copy_is_translated(language):
    from app.i18n import set_language, tr

    keys = ["Get Ollama", "Get LM Studio", "Open AI Settings",
            HINT_INSTALL, HINT_PULL]
    try:
        set_language(language)
        untranslated = [k for k in keys if tr(k) == k]
        assert untranslated == [], f"{language}: {[k[:40] for k in untranslated]}"
    finally:
        set_language("English")
