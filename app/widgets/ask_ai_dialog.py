"""On-demand single-item AI explanation dialog.

Used when the user has AI explanations switched off globally but wants an
answer about one specific file or entity. The request is issued immediately;
the answer streams back asynchronously via ``AIExplainer.finding_updated``.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextEdit,
)

from app.i18n import tr


class AskAIDialog(QDialog):
    """Ask the AI about one item and show the answer when it arrives.

    The dialog holds the *live* Finding/SmartEntity so the explainer's result
    lands on the same object the rest of the UI reads from.
    """

    def __init__(self, item, explainer, parent=None, facts: dict | None = None):
        super().__init__(parent)
        self._item = item
        self._explainer = explainer
        # What the caller measured about this item — a folder's real size and
        # file count. Sent with every request from this dialog, the first one
        # and each "Ask again", so a regenerated answer is held to the same
        # facts as the first.
        self._facts = dict(facts or {})
        self._connected = False

        self.setWindowTitle(tr("Ask AI"))
        self.setMinimumWidth(520)
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(8)

        name = QLabel(getattr(item, "name", "") or tr("Item"))
        name.setStyleSheet("font-size: 14px; font-weight: bold;")
        name.setWordWrap(True)
        root.addWidget(name)

        path = QLabel(getattr(item, "path", ""))
        path.setObjectName("Muted")
        path.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 10px;")
        path.setWordWrap(True)
        root.addWidget(path)

        self._status = QLabel("")
        self._status.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 11px;")
        self._status.setWordWrap(True)
        root.addWidget(self._status)

        self._body = QTextEdit()
        self._body.setReadOnly(True)
        self._body.setMinimumHeight(160)
        self._body.setStyleSheet(
            "QTextEdit { font-family: 'JetBrains Mono'; font-size: 12px; }"
        )
        self._body.setVisible(False)
        root.addWidget(self._body, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        # Regenerating lives here rather than on the row behind the dialog: the
        # row reopens this window, and the decision to spend a model run again
        # belongs next to the answer you are dissatisfied with.
        self._btn_again = QPushButton(tr("Ask again"))
        self._btn_again.setCursor(Qt.PointingHandCursor)
        self._btn_again.setToolTip(
            tr("Generate a new explanation and replace the saved one"))
        self._btn_again.setVisible(False)
        self._btn_again.clicked.connect(self._on_ask_again)
        btn_row.addWidget(self._btn_again)
        btn_row.addStretch()
        self._btn_close = QPushButton(tr("Close"))
        self._btn_close.setCursor(Qt.PointingHandCursor)
        self._btn_close.clicked.connect(self.reject)
        btn_row.addWidget(self._btn_close)
        root.addLayout(btn_row)

        self._begin()

    # ── Request lifecycle ────────────────────────────────────────────

    def _begin(self):
        """Show a cached answer if we already have one, otherwise ask."""
        if getattr(self._item, "ai_status", "") in ("ready", "done") and \
                getattr(self._item, "ai_explanation", ""):
            self._show_answer(self._item.ai_explanation)
            return

        # Listen before requesting so a fast (cached) result isn't missed.
        self._explainer.finding_updated.connect(self._on_updated)
        self._connected = True

        reason = self._explainer.explain_item(self._item, facts=self._facts)
        if reason == "no-model":
            self._disconnect()
            self._status.setText(
                tr("Select an AI model in Settings to use Ask AI.")
            )
            return
        self._status.setText(tr("Analyzing…"))

    def _on_updated(self, updated):
        """Only react to *our* item; other queue items may finish meanwhile."""
        if updated is not self._item:
            return
        status = getattr(updated, "ai_status", "")
        if status in ("ready", "done") and getattr(updated, "ai_explanation", ""):
            self._show_answer(updated.ai_explanation)
        elif status in ("failed", "error"):
            self._disconnect()
            err = getattr(updated, "ai_error", "") or tr("Unknown error")
            self._status.setText(
                tr("Reasoning is not available right now: {error}").format(error=err)
            )

    def _on_ask_again(self):
        """Generate a new explanation over the top of the stored one."""
        self._btn_again.setEnabled(False)
        self._status.setText(tr("Analyzing…"))
        if not self._connected:
            self._explainer.finding_updated.connect(self._on_updated)
            self._connected = True
        reason = self._explainer.explain_item(self._item, force_refresh=True,
                                              facts=self._facts)
        if reason == "no-model":
            self._disconnect()
            self._btn_again.setEnabled(True)
            self._status.setText(
                tr("Select an AI model in Settings to use Ask AI."))

    def _show_answer(self, text: str):
        self._disconnect()
        self._status.setText("")
        self._body.setPlainText(text)
        self._body.setVisible(True)
        # Only once there is something to replace.
        self._btn_again.setEnabled(True)
        self._btn_again.setVisible(True)

    def _disconnect(self):
        if not self._connected:
            return
        try:
            self._explainer.finding_updated.disconnect(self._on_updated)
        except (RuntimeError, TypeError):
            pass
        self._connected = False

    # Ensure we never leave a dangling connection into a destroyed dialog.
    def reject(self):
        self._disconnect()
        super().reject()

    def closeEvent(self, event):
        self._disconnect()
        super().closeEvent(event)
