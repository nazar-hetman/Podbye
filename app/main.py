"""Vigil / LootCleaner — Main application entry point."""

import sys
import os
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
from app.themes.theme_manager import build_qss

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
        self.setWindowTitle("VIGIL — LootCleaner")
        self.setMinimumSize(1100, 700)
        self.resize(1440, 900)
        self._current_theme = "forest"
        self._current_screen_name = "Home"
        self._settings_store = SettingsStore()
        init_language(self._settings_store)
        self._scan_state = ScanState(self)
        self._scan_state.set_settings_store(self._settings_store)
        self._ai_explainer = AIExplainer(self._settings_store, parent=self)
        self._scan_state.set_ai_explainer(self._ai_explainer)
        self._connect_shell_status_signals()
        self._build_ui()
        self._navigate("Home")
        saved_theme = self._settings_store.get("theme", "forest")
        self._apply_theme(saved_theme)

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

        # Replace central widget; schedule old one for deletion
        self.setCentralWidget(central)
        if old_central:
            old_central.deleteLater()

        self._refresh_shell_chrome()

    def _add_screen(self, name: str, widget: QWidget):
        self._screens[name] = widget
        self._stack.addWidget(widget)

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

    def _apply_theme(self, theme_key: str):
        self._current_theme = theme_key
        qss = build_qss(theme_key)
        QApplication.instance().setStyleSheet(qss)
        self._refresh_shell_chrome()

    def _on_settings_saved(self):
        """Handle settings save: refresh sidebar and apply language change live."""
        self._refresh_shell_chrome()
        from app.i18n import get_language, set_language
        stored_lang = self._settings_store.get("ui_language", "English")
        if stored_lang != get_language():
            if self._scan_state.is_running:
                # Scan is active — language will apply on next launch
                return
            set_language(stored_lang)
            self._build_ui()
            self._navigate(self._current_screen_name)
            self._apply_theme(self._current_theme)

    def _connect_shell_status_signals(self):
        """Keep the shared shell chrome in sync with scan and AI activity."""
        self._scan_state.scan_started.connect(lambda *_: self._refresh_shell_chrome())
        self._scan_state.scan_finished.connect(self._refresh_shell_chrome)
        self._scan_state.scan_halted.connect(self._refresh_shell_chrome)
        self._scan_state.scan_phase_changed.connect(lambda *_: self._refresh_shell_chrome())
        self._scan_state.ui_refresh.connect(self._refresh_shell_chrome)
        self._ai_explainer.queue_started.connect(self._refresh_shell_chrome)
        self._ai_explainer.queue_finished.connect(self._refresh_shell_chrome)
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

    def closeEvent(self, event):
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

        # Halt any QThread workers parented under a screen
        # (ScanWorker, DuplicateDetector, StartupAIWorker,
        # QuickCleanupDetector, CleanupWorker, ...).
        from PySide6.QtCore import QThread
        for screen in self._screens.values():
            for thread in screen.findChildren(QThread):
                if not thread.isRunning():
                    continue
                for method_name in ("cancel", "halt", "stop"):
                    if hasattr(thread, method_name):
                        try:
                            getattr(thread, method_name)()
                        except Exception:
                            pass
                        break
                thread.wait(500)

        super().closeEvent(event)


def main():
    # High-DPI attribute (Qt 5 compat, harmless on Qt 6)
    try:
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except AttributeError:
        pass

    app = QApplication(sys.argv)

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
