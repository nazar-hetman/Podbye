"""Vigil — Main application entry point."""

import sys
import os
import threading
import time
import logging

# Ensure the project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ─── Logging ───
# Quiet by default; set the VIGIL_DEBUG environment variable to 1 to surface
# internal lifecycle/thread breadcrumbs on the console.
logging.basicConfig(
    level=logging.DEBUG if os.environ.get("VIGIL_DEBUG") else logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

# ─── High-DPI scaling (must be set before QApplication) ───
os.environ["QT_ENABLE_HIGHDPI_SCALING"]   = "1"
os.environ["QT_SCALE_FACTOR_ROUNDING_POLICY"] = "PassThrough"

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QStackedWidget,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont

from app.fonts import load_fonts, FONT_UI
from app.widgets.sidebar import Sidebar
from app.widgets.topbar import Topbar
from app.widgets.logo import logo_icon
from app.widgets.tray import VigilTray
from app.widgets.close_dialog import (
    CloseRunningDialog, OUTCOME_QUIT, OUTCOME_BACKGROUND,
)
from app.themes.theme_manager import build_qss, get_palette

from app.screens.home import HomeScreen
from app.screens.quick_cleanup import QuickCleanupScreen
from app.screens.analyze import AnalyzeScreen
from app.screens.findings_dashboard import FindingsDashboard
from app.screens.startups import StartupsScreen
from app.screens.history import HistoryScreen
from app.screens.settings import SettingsScreen
from app.state.scan_state import ScanState
from app.config.settings_store import SettingsStore
from app.services.ai_explainer import AIExplainer
from app.i18n import init_language, tr


def _screen_subtitles() -> dict:
    return {
        "Home":          tr("Session overview"),
        "Quick Cleanup": tr("Fast confidence-based safe cleanup"),
        "Analyze":       tr("Folder / drive / custom path analysis"),
        "Findings":      tr("Review findings and decide actions"),
        "Startups":      tr("Recommendation-only startup analysis"),
        "History":       tr("Analysis session history"),
        "Settings":      tr("Configuration"),
    }


class VigilWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("VIGIL")
        self.setMinimumSize(1100, 700)
        self.resize(1440, 900)
        self._current_theme = "forest"
        self._current_screen_name = "Home"
        # Background / tray state.
        self._tray: VigilTray | None = None
        self._force_quit = False        # set by the tray "Quit" action
        self._in_background = False     # window hidden, work continuing in tray
        self._tray_intro_shown = False  # one-time "still running" balloon
        self._bg_done_notified = False  # completion notice fired for this stint
        self._pending_lang = None       # deferred UI language (set mid-analysis)
        self._settings_store = SettingsStore()
        init_language(self._settings_store)
        self._sweep_session_leftovers()
        self._scan_state = ScanState(self)
        self._scan_state.set_settings_store(self._settings_store)
        self._ai_explainer = AIExplainer(self._settings_store, parent=self)
        self._scan_state.set_ai_explainer(self._ai_explainer)
        self._connect_shell_status_signals()
        self._build_ui()
        self._navigate("Home")
        saved_theme = self._settings_store.get("theme", "forest")
        self._apply_theme(saved_theme)

    def _sweep_session_leftovers(self):
        """Reclaim crash leftovers in the sessions folder, once per launch.

        Vigil protects its own data folder from cleanup, so nothing else will
        ever reclaim these. Unlinking is O(1) whatever the file size, and only
        files older than an hour are touched, so this is safe on the UI thread.
        """
        try:
            from app.state.session_store import sweep_orphaned_files
            removed, reclaimed = sweep_orphaned_files()
            if removed:
                logging.info("[session] swept %d leftover file(s), %.1f MB reclaimed",
                             removed, reclaimed / (1024 * 1024))
        except Exception:
            logging.exception("[session] leftover sweep failed")
        self._compact_oversized_sessions()

    def _compact_oversized_sessions(self):
        """Shrink retained sessions whose findings the loader already discards.

        Off the UI thread, unlike the sweep above: compaction byte-scans each
        oversized file end to end, and the profile that prompted this held
        3.42 GB across three of them — seconds of scanning before the window
        would have appeared. A daemon thread killed mid-write only orphans a
        temp file, which the next launch's sweep reclaims.
        """
        def _run():
            try:
                from app.state.session_store import compact_oversized_sessions
                count, reclaimed = compact_oversized_sessions()
                if count:
                    logging.info("[session] compacted %d oversized session(s), "
                                 "%.1f MB reclaimed", count, reclaimed / (1024 * 1024))
            except Exception:
                logging.exception("[session] compaction failed")

        threading.Thread(target=_run, name="session-compact", daemon=True).start()

    def _build_ui(self):
        """Build (or rebuild) the entire UI. Safe to call again for language changes."""
        old_central = self.centralWidget()

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Sidebar — rebuilt each time so section/nav labels pick up new language
        self._sidebar = Sidebar()
        self._sidebar.screen_changed.connect(self._navigate)
        root.addWidget(self._sidebar)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)

        self._topbar = Topbar()
        right.addWidget(self._topbar)

        self._stack = QStackedWidget()
        self._screens = {}

        home = HomeScreen()
        home.set_scan_state(self._scan_state)
        self._add_screen("Home", home)

        quick_cleanup = QuickCleanupScreen()
        quick_cleanup.set_settings_store(self._settings_store)
        self._add_screen("Quick Cleanup", quick_cleanup)

        analyze = AnalyzeScreen()
        analyze.set_scan_state(self._scan_state)
        self._add_screen("Analyze", analyze)

        findings = FindingsDashboard()
        findings.set_scan_state(self._scan_state)
        self._add_screen("Findings", findings)

        startups = StartupsScreen()
        startups.set_settings_store(self._settings_store)
        self._add_screen("Startups", startups)

        history = HistoryScreen()
        self._add_screen("History", history)

        settings = SettingsScreen(
            theme_callback=self._apply_theme,
            settings_store=self._settings_store,
        )
        self._add_screen("Settings", settings)

        right.addWidget(self._stack, stretch=1)
        root.addLayout(right)

        # Wire inter-screen signals
        home.navigate_to.connect(self._navigate)
        home.resume_requested.connect(self._on_resume_requested)
        home.start_new_requested.connect(self._on_start_new_requested)
        home.stop_requested.connect(self._on_stop_from_home)
        home.open_findings_requested.connect(self._on_open_findings_requested)

        quick_cleanup.navigate_to.connect(self._navigate)
        findings.navigate_to_analyze.connect(lambda: self._navigate("Analyze"))

        analyze_screen = self._screens["Analyze"]
        analyze_screen.category_clicked.connect(
            lambda cat: self._navigate_to_findings_category(cat)
        )

        history_screen = self._screens["History"]
        history_screen.open_session_requested.connect(self._on_open_findings_requested)
        history_screen.rerun_requested.connect(self._on_rerun_from_history)

        settings.settings_saved.connect(self._on_settings_saved)

        # Replace central widget; hide the old one before scheduling its
        # deletion. setCentralWidget only drops it from the layout — left
        # visible, the previous language's entire UI paints over the new one
        # until the deferred delete runs. Hide rather than setParent(None):
        # unparenting would make the whole outgoing UI a top-level window.
        self.setCentralWidget(central)
        if old_central:
            # Safety net, not the primary guard. Deleting this tree destroys
            # every QThread parented to a screen inside it, and destroying a
            # RUNNING QThread aborts the process (0xC0000409) with no
            # traceback. Callers are expected to have stopped work first; this
            # makes it impossible to crash even when one forgets.
            self._stop_all_background_work()
            old_central.hide()
            old_central.deleteLater()

        self._refresh_shell_chrome()

    def _add_screen(self, name: str, widget: QWidget):
        self._screens[name] = widget
        self._stack.addWidget(widget)

    # ── Background work across screens ────────────────────────────

    def _busy_reason(self) -> str:
        """What is running that a shell rebuild would destroy, or "".

        Screens own their own threads, so each one answers for itself.
        """
        for screen in self._screens.values():
            reason = getattr(screen, "busy_reason", None)
            if callable(reason):
                try:
                    text = reason()
                except RuntimeError:
                    continue
                if text:
                    return text
        return ""

    def _stop_all_background_work(self, timeout_ms: int = 3000) -> bool:
        stopped = True
        for screen in list(self._screens.values()):
            stop = getattr(screen, "stop_background_work", None)
            if callable(stop):
                try:
                    stopped = bool(stop(timeout_ms)) and stopped
                except RuntimeError:
                    pass
        return stopped

    def _navigate(self, name: str):
        if name in self._screens:
            self._current_screen_name = name
            self._stack.setCurrentWidget(self._screens[name])
            self._sidebar.set_screen(name)
            subtitle = _screen_subtitles().get(name, "")
            self._topbar.set_screen(name, subtitle)
            if name == "Home":
                self._screens["Home"].refresh()
            elif name == "History":
                self._screens["History"].refresh()
            elif name == "Settings":
                self._screens["Settings"].reload_close_behavior()

    def _apply_theme(self, theme_key: str):
        self._current_theme = theme_key
        # setStyleSheet re-polishes every live widget — ~1s with all screens
        # built — and it blocks the UI thread. A wait cursor was tried here to
        # show it was working; it did the opposite, reading as a freeze about
        # to crash. The honest fix is to make it fast (build screens lazily),
        # not to decorate the stall.
        app = QApplication.instance()
        qss = build_qss(theme_key)
        app.setStyleSheet(qss)
        # Recolour the window / taskbar icon to match the theme accent.
        icon = logo_icon(get_palette(theme_key)["accent"])
        self.setWindowIcon(icon)
        QApplication.instance().setWindowIcon(icon)
        if self._tray is not None:
            self._tray.update_icon(icon)
        self._refresh_shell_chrome()

    def _on_settings_saved(self):
        """Handle settings save: refresh sidebar and apply language change live."""
        self._refresh_shell_chrome()
        from app.i18n import get_language
        stored_lang = self._settings_store.get("ui_language", "English")
        if stored_lang != get_language():
            # Rebuilding the whole UI tears down every screen and the threads
            # they own. Deferring covers the live scan/findings view, and —
            # the case that used to kill the process outright — a Quick
            # Cleanup deletion in flight.
            busy = (tr("an analysis is running")
                    if self._scan_state.is_analysis_active else self._busy_reason())
            if busy:
                # _maybe_apply_pending_language() picks it up on completion/stop.
                self._pending_lang = stored_lang
                self._start_pending_language_poll()
                self._warn_language_deferred(busy)
                return
            self._apply_language_change(stored_lang)

    def _apply_language_change(self, lang: str):
        """Activate *lang* and rebuild the shell so every screen re-translates."""
        from app.i18n import set_language
        self._pending_lang = None
        set_language(lang)
        self._build_ui()
        self._navigate(self._current_screen_name)
        self._apply_theme(self._current_theme)

    def _warn_language_deferred(self, reason: str = ""):
        """Tell the user the language switch waits for the work in progress."""
        from PySide6.QtWidgets import QMessageBox
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle(tr("Language change pending"))
        box.setText(
            tr("Vigil is busy: {reason}. The language will change "
               "automatically once that finishes or you stop it.",
               reason=reason or tr("an analysis is running"))
        )
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.exec()

    def _maybe_apply_pending_language(self, *args):
        """Apply a deferred language switch once the pipeline is fully idle.

        Connected to phase/AI completion signals. Guards on is_analysis_active
        and AI-running so the UI is never rebuilt mid-scan/entity/AI work, and
        defers the rebuild to the next event-loop tick to avoid tearing down
        screens from inside the signal that is still being dispatched.
        """
        from app.i18n import get_language
        if not self._pending_lang or self._pending_lang == get_language():
            self._pending_lang = None
            return
        if self._scan_state.is_analysis_active:
            return
        if self._ai_explainer and self._ai_explainer.is_running:
            return
        # A screen's own thread — a Quick Cleanup deletion, say — is not
        # visible in scan state, and rebuilding while one runs is fatal.
        # Nothing signals us when those finish, so poll until they do.
        if self._busy_reason():
            self._start_pending_language_poll()
            return
        self._stop_pending_language_poll()
        lang = self._pending_lang
        self._pending_lang = None
        QTimer.singleShot(0, lambda: self._apply_language_change(lang))

    def _start_pending_language_poll(self):
        timer = getattr(self, "_lang_poll_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setInterval(1000)
            timer.timeout.connect(self._maybe_apply_pending_language)
            self._lang_poll_timer = timer
        if not timer.isActive():
            timer.start()

    def _stop_pending_language_poll(self):
        timer = getattr(self, "_lang_poll_timer", None)
        if timer is not None and timer.isActive():
            timer.stop()

    def _connect_shell_status_signals(self):
        """Keep the shared shell chrome in sync with scan and AI activity."""
        self._scan_state.scan_started.connect(lambda *_: self._refresh_shell_chrome())
        self._scan_state.scan_finished.connect(self._refresh_shell_chrome)
        self._scan_state.scan_halted.connect(self._refresh_shell_chrome)
        self._scan_state.scan_phase_changed.connect(lambda *_: self._refresh_shell_chrome())
        self._scan_state.ui_refresh.connect(self._refresh_shell_chrome)
        self._ai_explainer.queue_started.connect(self._refresh_shell_chrome)
        self._ai_explainer.queue_finished.connect(self._refresh_shell_chrome)
        # Apply a deferred UI-language switch once analysis fully settles.
        self._scan_state.scan_phase_changed.connect(self._maybe_apply_pending_language)
        self._scan_state.scan_halted.connect(self._maybe_apply_pending_language)
        self._ai_explainer.queue_finished.connect(self._maybe_apply_pending_language)
        self._ai_explainer.queue_progress.connect(lambda *_: self._refresh_shell_chrome())

    def _shell_status_text(self) -> str:
        """Return the calm, user-facing footer status."""
        if self._ai_explainer and self._ai_explainer.is_running:
            return tr("AI active")
        if self._scan_state.is_analysis_active or self._scan_state.current_phase in ("filesystem", "entity_detection"):
            return tr("Scanning")
        if self._scan_state.current_phase == "ai_classification":
            return tr("AI active")
        if self._scan_state.total_count > 0 or self._scan_state.entity_count > 0:
            return tr("Complete")
        return tr("Ready")

    def _refresh_shell_chrome(self):
        """Push current model and app state into the shared shell chrome."""
        model = self._settings_store.get("ai_model", "")
        model_short = model.split(":")[0] if model else "—"
        right = f"MODEL · {model_short or '—'}"
        self._sidebar.update_status(status=self._shell_status_text())
        self._topbar.set_right_text(right)
        self._sync_tray()

    def _sync_tray(self):
        """Keep the tray tooltip current and announce completion once, quietly."""
        if not self._in_background or self._tray is None:
            return
        self._tray.set_status(self._shell_status_text())
        # Background work just wound down — give one calm heads-up.
        if not self._is_busy() and not self._bg_done_notified:
            self._bg_done_notified = True
            self._tray.notify(
                tr("Vigil finished"),
                tr("The task is done. Open Vigil to review, or quit from here."),
            )

    def _on_resume_requested(self, session_data: dict):
        """Resume an unfinished scan from the Home screen."""
        analyze = self._screens.get("Analyze")
        if analyze:
            self._navigate("Analyze")
            analyze.resume_scan(session_data)

    def _on_start_new_requested(self):
        """Navigate to Analyze for a fresh scan."""
        self._navigate("Analyze")
        analyze = self._screens.get("Analyze")
        if analyze and hasattr(analyze, "prepare_new_scan"):
            QTimer.singleShot(0, analyze.prepare_new_scan)

    def _on_stop_from_home(self):
        """Stop active scan from the Home screen."""
        analyze = self._screens.get("Analyze")
        if analyze and self._scan_state.is_running:
            analyze._stop_scan()

    def _on_rerun_from_history(self, target: str):
        """Navigate to Analyze and prefill the target from a history entry."""
        self._navigate("Analyze")
        analyze = self._screens.get("Analyze")
        if analyze and target and hasattr(analyze, "set_target"):
            analyze.set_target(target)

    def _on_open_findings_requested(self, session_data: dict):
        """Open Findings with restored session data — no re-analysis."""
        if self._scan_state:
            # Set restore mode so AI cache shows appropriate messages
            self._scan_state._run_mode = "restore"
            self._scan_state.restore_from_session(session_data)
        
        # Navigate to Findings
        self._navigate("Findings")

    def _navigate_to_findings_category(self, category: str):
        """Navigate to Findings screen with category filter (if available)."""
        self._navigate("Findings")
        # Call open_category on FindingsDashboard to directly show the category
        findings = self._screens.get("Findings")
        if findings and hasattr(findings, 'open_category'):
            findings.open_category(category)

    # ── Background / tray ─────────────────────────────────────────

    def _is_busy(self) -> bool:
        """True while a scan, entity detection or AI job is still running."""
        if self._scan_state.is_running:
            return True
        return bool(self._ai_explainer and self._ai_explainer.is_running)

    def _activity_label(self) -> str:
        """Noun phrase describing the running work, for the close dialog."""
        if self._ai_explainer and self._ai_explainer.is_running:
            return tr("AI analysis")
        if self._scan_state.is_running:
            return tr("A scan")
        return tr("A task")

    def _ensure_tray(self) -> "VigilTray | None":
        """Create the tray icon on first use; return None if unavailable."""
        if self._tray is not None:
            return self._tray
        if not VigilTray.is_available():
            return None
        icon = logo_icon(get_palette(self._current_theme)["accent"])
        self._tray = VigilTray(icon, parent=self)
        self._tray.show_requested.connect(self._restore_from_tray)
        self._tray.quit_requested.connect(self._quit_from_tray)
        return self._tray

    def _enter_background(self):
        """Hide the window to the tray, leaving background work running."""
        tray = self._ensure_tray()
        if tray is None:
            # No system tray available — keep work alive but stay minimized
            # rather than vanishing with no way back.
            self.showMinimized()
            return
        tray.set_status(self._shell_status_text())
        tray.show()
        self._in_background = True
        self._bg_done_notified = False
        self.hide()
        if not self._tray_intro_shown:
            self._tray_intro_shown = True
            tray.notify(
                tr("Vigil is still running"),
                tr("Your task continues in the background. "
                   "Right-click the tray icon to quit."),
            )

    def _restore_from_tray(self):
        """Bring the window back from the tray."""
        self._in_background = False
        if self._tray is not None:
            self._tray.hide()
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit_from_tray(self):
        """Tray 'Quit' — force a real shutdown via closeEvent."""
        self._force_quit = True
        self.close()

    def _shutdown(self):
        """Save session state and halt background work before the app exits.

        Qt will warn ("QThread: Destroyed while thread is still running") and
        can crash on Windows if a parented QThread is alive when its parent
        widget is destroyed. We cancel/halt every running worker and give it
        a short window to exit cleanly.
        """
        if self._scan_state.is_running:
            self._scan_state.save_session_final("running")

        # Cancel scan, entity detection and AI queue managed by ScanState.
        try:
            self._scan_state.stop_all()
        except Exception:
            pass

        # Hide immediately so the close feels instant while workers wind down
        # in the background, rather than looking frozen.
        if self.isVisible():
            self.hide()
            QApplication.processEvents()

        from PySide6.QtCore import QThread

        # Phase 1 — ask every worker to stop and mute its late signals.
        # Workers (ScanWorker, DuplicateDetector, StartupAIWorker,
        # QuickCleanupDetector, CleanupWorker, ...) only check their cancel
        # flag at loop boundaries, so they may not stop instantly.
        # Search from the window rather than per-screen: that also covers
        # workers owned by dialogs (e.g. CleanupWorker in the cleanup dialog),
        # which are parented to the dialog and so never appeared in a
        # screen-only sweep.
        running = []
        for thread in self.findChildren(QThread):
            if not thread.isRunning():
                continue
            # Block signals first so no callback fires into a screen that
            # is about to be torn down.
            thread.blockSignals(True)
            for method_name in ("cancel", "halt", "stop"):
                if hasattr(thread, method_name):
                    try:
                        getattr(thread, method_name)()
                    except Exception:
                        pass
                    break
            running.append(thread)

        # Phase 2 — wait for a graceful exit, pumping the event loop so the UI
        # stays responsive and queued work can drain. Bounded total budget.
        deadline = time.monotonic() + 3.0
        for thread in running:
            while thread.isRunning() and time.monotonic() < deadline:
                thread.wait(50)
                QApplication.processEvents()

        # Phase 3 — force-stop any straggler. A QThread that is still running
        # when its parent is destroyed aborts the process on Windows, so this
        # last resort is what actually prevents the close-while-busy crash.
        for thread in running:
            if thread.isRunning():
                try:
                    thread.terminate()
                    thread.wait(1000)
                except Exception:
                    pass

        # Session saves run on daemon threads, which the interpreter kills at
        # exit — without this join, the session the user just stopped can be
        # dropped on the floor. Bounded so a stuck write can't hang the close.
        try:
            self._scan_state.wait_for_saves()
        except Exception:
            pass

        if self._tray is not None:
            self._tray.hide()

    def closeEvent(self, event):
        """Route a window close through the user's close-behavior preference.

        When nothing is running (or a real quit was requested), shut down and
        exit. Otherwise honour `close_behavior`: prompt, always background, or
        always quit.
        """
        if self._force_quit or not self._is_busy():
            self._shutdown()
            event.accept()
            QApplication.instance().quit()
            return

        behavior = self._settings_store.get("close_behavior", "ask")

        if behavior == "quit":
            self._shutdown()
            event.accept()
            QApplication.instance().quit()
            return

        if behavior == "background":
            self._enter_background()
            event.ignore()
            return

        # "ask" — prompt the user.
        dlg = CloseRunningDialog(activity_label=self._activity_label(), parent=self)
        dlg.exec()
        remembered = dlg.persisted_setting()
        if remembered:
            self._settings_store.set_and_save("close_behavior", remembered)

        if dlg.outcome == OUTCOME_QUIT:
            self._shutdown()
            event.accept()
            QApplication.instance().quit()
        elif dlg.outcome == OUTCOME_BACKGROUND:
            self._enter_background()
            event.ignore()
        else:  # OUTCOME_CANCEL — stay open and visible
            event.ignore()


def main():
    # High-DPI attribute (Qt 5 compat, harmless on Qt 6)
    try:
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except AttributeError:
        pass

    # Windows: bind an explicit AppUserModelID so the taskbar shows our window
    # icon instead of the generic Python launcher icon.
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "Vigil.App"
            )
        except Exception:
            pass

    app = QApplication(sys.argv)
    # The window can hide to the system tray while background work continues,
    # so closing the last window must not auto-quit the app.
    app.setQuitOnLastWindowClosed(False)

    # Load bundled fonts
    load_fonts()

    # Set Inter as default UI font
    font = QFont(FONT_UI, 10)
    font.setHintingPreference(QFont.PreferFullHinting)
    app.setFont(font)

    window = VigilWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
