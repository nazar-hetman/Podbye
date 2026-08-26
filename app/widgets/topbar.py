"""Top bar widget for Podbye — global app bar with centered identity and AI model."""

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel
from PySide6.QtCore import Qt

from app.i18n import tr


class Topbar(QFrame):
    """Horizontal top bar with centered product identity and compact model info."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Topbar")
        self.setFixedHeight(38)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(0)

        layout.addStretch()

        # The name and its tagline live in the sidebar, an inch to the left.
        # Repeating them here gave the window two competing descriptors.
        self._center = QLabel(tr("LOCAL SYSTEM ANALYSIS"))
        self._center.setObjectName("Dim")
        self._center.setStyleSheet(
            "font-family: 'Silkscreen', 'JetBrains Mono'; font-size: 10px; letter-spacing: 3px;"
        )
        self._center.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._center)

        layout.addStretch()

        self._right = QLabel("")
        self._right.setObjectName("Dim")
        self._right.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 10px; letter-spacing: 0px;")
        layout.addWidget(self._right)

    def set_screen(self, title: str, subtitle: str = "", right_text: str = ""):
        # Title bar is static in this design; screen title lives inside each screen
        pass

    def set_right_text(self, text: str):
        self._right.setText(text)
