"""Reusable panel and card widgets for Vigil."""

from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QSizePolicy, QWidget,
)
from PySide6.QtCore import Qt

_TACTICAL_FAMILY = "'Silkscreen', 'JetBrains Mono'"


def tactical_style(
    font_size: int = 10,
    letter_spacing: int = 2,
    color: str = "",
    padding: str = "0",
) -> str:
    """Return a restrained Silkscreen-based style string for tactical labels."""
    style = (
        f"font-family: {_TACTICAL_FAMILY}; "
        f"font-size: {font_size}px; "
        f"letter-spacing: {letter_spacing}px; "
        f"padding: {padding};"
    )
    if color:
        style += f" color: {color};"
    return style


def apply_tactical_label(
    label: QLabel,
    *,
    font_size: int = 10,
    letter_spacing: int = 2,
    color: str = "",
    padding: str = "0",
):
    """Apply the shared tactical header style to an existing label."""
    label.setStyleSheet(
        tactical_style(
            font_size=font_size,
            letter_spacing=letter_spacing,
            color=color,
            padding=padding,
        )
    )


class Panel(QFrame):
    """A bordered panel container."""

    def __init__(self, parent=None, alt=False):
        super().__init__(parent)
        self.setObjectName("PanelAlt" if alt else "Panel")

    def with_layout(self, vertical=True, margins=(14, 12, 14, 12), spacing=8):
        if vertical:
            lay = QVBoxLayout(self)
        else:
            lay = QHBoxLayout(self)
        lay.setContentsMargins(*margins)
        lay.setSpacing(spacing)
        return lay


class StatCard(QFrame):
    """A small stat card with label + big number."""

    def __init__(self, label: str, value: str, parent=None):
        super().__init__(parent)
        self.setObjectName("Panel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(4)

        lbl = QLabel(label.upper())
        lbl.setObjectName("SectionHeader")
        layout.addWidget(lbl)

        val = QLabel(value)
        val.setObjectName("BigNumber")
        val.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 26px; font-weight: bold;")
        layout.addWidget(val)
        self._value_label = val

    def set_value(self, value: str):
        self._value_label.setText(value)


class SectionHeader(QLabel):
    """A styled section header label."""

    def __init__(self, text: str, parent=None):
        super().__init__(text.upper(), parent)
        self.setObjectName("SectionHeader")
        apply_tactical_label(self, font_size=10, letter_spacing=2, padding="4px 0px")


class InfoRow(QFrame):
    """A horizontal key: value row."""

    def __init__(self, key: str, value: str, parent=None, mono_value=False):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(10)

        k = QLabel(key)
        k.setObjectName("Dim")
        k.setStyleSheet("font-size: 12px;")
        k.setFixedWidth(140)
        layout.addWidget(k)

        v = QLabel(value)
        v.setStyleSheet("font-size: 13px;" + (" font-family: 'JetBrains Mono';" if mono_value else ""))
        layout.addWidget(v)
        layout.addStretch()
        self._value_label = v

    def set_value(self, value: str):
        self._value_label.setText(value)
