"""Theme manager for Vigil — loads QSS files and exposes palette constants.

Typography:
  body_font   = Inter          — UI text, labels, buttons
  mono_font   = JetBrains Mono — tables, paths, metrics, feeds
  pixel_font  = Silkscreen     — tiny section headers, badges, wordmark
"""

import os

from PySide6.QtCore import QObject, Signal

THEME_DIR = os.path.dirname(os.path.abspath(__file__))


class _ThemeSignaller(QObject):
    """Module-level broadcaster — screens subscribe once in their constructor."""
    theme_changed = Signal(str)   # emits theme_key after each theme switch


_signaller = _ThemeSignaller()


def theme_signaller() -> _ThemeSignaller:
    """Return the singleton signaller for theme-change subscriptions."""
    return _signaller

# ─── Font family names (must match loaded TTF family names) ───
_BODY  = "Inter"
_MONO  = "JetBrains Mono"
_PIXEL = "Silkscreen"

PALETTES = {
    "forest": {
        "bg":           "#0c1511",
        "bg_deep":      "#070c09",
        "panel":        "#151e18",
        "panel_alt":    "#19231c",
        "panel_hover":  "#1f2c24",
        "border":       "#25332b",
        "border_alt":   "#32443a",
        "border_hover": "#486252",
        "text":         "#d9e1db",
        "text_dim":     "#93a297",
        "text_faint":   "#5f6f65",
        "accent":       "#7aa88a",
        "accent_hover": "#96bd9f",
        "accent_soft":  "#1c2d23",
        "on_accent":    "#070c09",
        "safe":         "#7aa88a",
        "review":       "#c7a66c",
        "risk":         "#c67a69",
        "optional":     "#6e93a8",
        "safe_soft":    "#1c2d23",
        "review_soft":  "#2a2316",
        "risk_soft":    "#2a1d1a",
        "optional_soft": "#18262d",
        "tint_bg":      "#101a15",
        "header_font":  _PIXEL,
        "body_font":    _BODY,
        "mono_font":    _MONO,
    },
    "amber": {
        "bg":           "#14100a",
        "bg_deep":      "#0b0805",
        "panel":        "#1d160d",
        "panel_alt":    "#231b10",
        "panel_hover":  "#2b2114",
        "border":       "#302517",
        "border_alt":   "#40311e",
        "border_hover": "#61492a",
        "text":         "#ebdbbd",
        "text_dim":     "#aa9370",
        "text_faint":   "#6b5941",
        "accent":       "#d79c54",
        "accent_hover": "#e4b572",
        "accent_soft":  "#2b2114",
        "on_accent":    "#0b0805",
        "safe":         "#b0bc6a",
        "review":       "#d79c54",
        "risk":         "#cb785d",
        "optional":     "#7e96a4",
        "safe_soft":    "#222613",
        "review_soft":  "#2b2114",
        "risk_soft":    "#2a1a13",
        "optional_soft": "#1c2228",
        "tint_bg":      "#17120b",
        "header_font":  _PIXEL,
        "body_font":    _BODY,
        "mono_font":    _MONO,
    },
    "mono": {
        "bg":           "#080808",
        "bg_deep":      "#030303",
        "panel":        "#101010",
        "panel_alt":    "#171717",
        "panel_hover":  "#1f1f1f",
        "border":       "#202020",
        "border_alt":   "#313131",
        "border_hover": "#4a4a4a",
        "text":         "#f0f0f0",
        "text_dim":     "#9a9a9a",
        "text_faint":   "#606060",
        "accent":       "#d9d9d9",
        "accent_hover": "#f2f2f2",
        "accent_soft":  "#1a1a1a",
        "on_accent":    "#050505",
        "safe":         "#86a89f",
        "review":       "#bea26f",
        "risk":         "#bb786c",
        "optional":     "#8c9ba4",
        "safe_soft":    "#121918",
        "review_soft":  "#1a1711",
        "risk_soft":    "#1a1311",
        "optional_soft": "#15181b",
        "tint_bg":      "#0d0d0d",
        "header_font":  _PIXEL,
        "body_font":    _BODY,
        "mono_font":    _MONO,
    },
    "paper": {
        "bg":           "#ece5d8",
        "bg_deep":      "#dbd1c0",
        "panel":        "#f3ede1",
        "panel_alt":    "#e6ddcd",
        "panel_hover":  "#ddd4c4",
        "border":       "#bfb29f",
        "border_alt":   "#9f907d",
        "border_hover": "#817360",
        "text":         "#232621",
        "text_dim":     "#636558",
        "text_faint":   "#8d897d",
        "accent":       "#6a7562",
        "accent_hover": "#525b4d",
        "accent_soft":  "#d8cfbe",
        "on_accent":    "#f3ede1",
        "safe":         "#5c7358",
        "review":       "#92714a",
        "risk":         "#8f5d52",
        "optional":     "#4f6678",
        "safe_soft":    "#dad1c1",
        "review_soft":  "#e3d8c3",
        "risk_soft":    "#e2d3cb",
        "optional_soft": "#d6dde1",
        "tint_bg":      "#e2d9c8",
        "header_font":  _PIXEL,
        "body_font":    _BODY,
        "mono_font":    _MONO,
    },
}

THEME_NAMES = ["Forest", "Amber", "Black", "Paper"]
THEME_KEYS  = ["forest", "amber", "mono", "paper"]

_current_theme_key = "forest"


# ─── Per-theme category color palettes ───────────────────────────
# Each palette preserves semantic identity (Media=purple-ish, Apps=blue,
# Documents=green, etc.) but transforms saturation, brightness, and
# warmth to harmonise with the active theme background.

_CATEGORY_COLORS = {
    "forest": {
        # Muted tactical — dark greens, navies, violets on near-black bg
        "Media":                "#4a1a6b",   # deep violet-purple
        "Applications":         "#0d3a5c",   # steel navy
        "Documents":            "#1a3d2b",   # dark forest green
        "Dev Artifacts":        "#1c3a4a",   # muted steel cyan
        "AI / ML":              "#2d1a4a",   # deep violet
        "Databases & Saves":    "#1a2a4a",   # slate blue
        "Cache & Temp":         "#4a2500",   # dark amber-orange
        "Archives":             "#3d2a1a",   # dark warm brown
        "Browser Data":         "#0a2a4a",   # deep ocean blue
        "System":               "#4a0d0d",   # dark crimson
        "System Logs":          "#2a2010",   # muted olive-gray
        "Unknown":              "#1e2832",   # dark neutral blue-gray
        "Other":                "#252d2a",   # very dark neutral
        "Protected / Restricted": "#5a0f0f", # dark crimson
        "Games":                "#143a26",   # deep emerald
        "Application Data":     "#16304a",   # muted navy
        "User Profile":         "#2e2a14",   # warm olive
        "Virtual Machines":     "#20304a",   # steel blue
        "Duplicates":           "#3a2a0a",   # dark amber
    },
    "amber": {
        # Warm tactical — bronze/olive/ochre tints
        "Media":                "#4a2860",   # warm dusty violet
        "Applications":         "#1a3050",   # warm steel blue
        "Documents":            "#2a3818",   # olive-green
        "Dev Artifacts":        "#1e3430",   # warm steel teal
        "AI / ML":              "#321a38",   # warm deep purple
        "Databases & Saves":    "#1e2838",   # warm dark blue
        "Cache & Temp":         "#4a2c00",   # dark amber
        "Archives":             "#3c2c14",   # dark warm brown
        "Browser Data":         "#0e2440",   # warm dark ocean
        "System":               "#42100a",   # dark warm crimson
        "System Logs":          "#2e2408",   # dark khaki
        "Unknown":              "#2a2418",   # warm dark neutral
        "Other":                "#262018",   # warm near-black
        "Protected / Restricted": "#501008", # deep burnt red
        "Games":                "#2a3a1a",   # warm olive-green
        "Application Data":     "#1a2838",   # warm dark blue
        "User Profile":         "#34280f",   # dark ochre
        "Virtual Machines":     "#1e2a40",   # warm steel
        "Duplicates":           "#3a2a0c",   # dark amber
    },
    "mono": {
        # Grayscale — subtle lightness differences only
        "Media":                "#323232",   # mid-dark gray
        "Applications":         "#2a2a2a",   # darker gray
        "Documents":            "#2e2e2e",   # dark gray
        "Dev Artifacts":        "#272727",   # very dark gray
        "AI / ML":              "#303030",   # mid gray
        "Databases & Saves":    "#262626",   # dark
        "Cache & Temp":         "#3a3a3a",   # slightly lighter
        "Archives":             "#353535",   # mid-dark
        "Browser Data":         "#242424",   # very dark
        "System":               "#1e1e1e",   # near-black
        "System Logs":          "#222222",   # near-black
        "Unknown":              "#1a1a1a",   # darkest
        "Other":                "#1c1c1c",   # darkest
        "Protected / Restricted": "#404040", # lighter gray for contrast
        "Games":                "#2c2c2c",   # mid-dark gray
        "Application Data":     "#242424",   # dark gray
        "User Profile":         "#2e2e2e",   # mid gray
        "Virtual Machines":     "#282828",   # dark gray
        "Duplicates":           "#383838",   # lighter gray
    },
    "paper": {
        # Paper / dusty — soft muted tones on warm light bg
        # Use deeper/saturated tones so they read against #faf6ec
        "Media":                "#7b4a9e",   # dusty lavender
        "Applications":         "#2e5f8a",   # muted steel blue
        "Documents":            "#3a6b42",   # muted forest green
        "Dev Artifacts":        "#2e5c6e",   # dusty teal
        "AI / ML":              "#5e3878",   # muted purple
        "Databases & Saves":    "#2a4a6e",   # muted slate
        "Cache & Temp":         "#8a5a1a",   # warm dark amber
        "Archives":             "#7a4a28",   # warm brown
        "Browser Data":         "#1e4a70",   # dusty ocean
        "System":               "#8a2020",   # muted crimson
        "System Logs":          "#5a5020",   # muted olive
        "Unknown":              "#5a5e58",   # muted warm gray
        "Other":                "#52564e",   # slightly darker
        "Protected / Restricted": "#922020", # deep muted red
        "Games":                "#3a7a4a",   # muted emerald
        "Application Data":     "#3a5a7a",   # dusty navy
        "User Profile":         "#6a5a3a",   # warm tan
        "Virtual Machines":     "#4a5a7a",   # dusty steel
        "Duplicates":           "#9a6a2a",   # warm amber
    },
}


def get_category_colors(theme_key: str = None) -> dict:
    """Return the category color map for the given theme (defaults to active theme)."""
    if theme_key is None:
        theme_key = _current_theme_key
    return _CATEGORY_COLORS.get(theme_key, _CATEGORY_COLORS["forest"])


def get_palette(theme_key: str = None) -> dict:
    if theme_key is None:
        theme_key = _current_theme_key
    return PALETTES.get(theme_key, PALETTES["forest"])


def build_qss(theme_key: str) -> str:
    """Build complete QSS for the given theme key."""
    global _current_theme_key
    _current_theme_key = theme_key
    p = get_palette(theme_key)
    qss = _BASE_QSS.format(**p)
    _signaller.theme_changed.emit(theme_key)
    return qss


# ─────────────────────────────────────────────────────────
#  Master QSS template — matched to Vigil design system
#
#  Typography hierarchy (design):
#    64px  mono 300   — hero big number
#    48px  mono 300   — secondary big number
#    36px  mono 300   — stat card number
#    14px  pixel      — screen crumb / wordmark
#    13px  body 500   — primary body text, nav items
#    12px  body 500   — buttons, labels, secondary
#    11px  body       — chips
#    10px  pixel/mono — eyebrows, headers (uppercase)
#     9px  pixel      — build version, tiny labels
#
#  Color philosophy:
#    {text}       — primary text
#    {text_dim}   — secondary descriptions, help text
#    {text_faint} — muted, timestamps, disabled, footnotes
#    {accent}     — highlights, active states, primary button bg
# ─────────────────────────────────────────────────────────

_BASE_QSS = """
/* ─── Vigil — Generated QSS — Design System v2 ─── */

* {{
    margin: 0;
    padding: 0;
    outline: none;
}}

/* QMainWindow paints the page background. Plain QWidgets inherit colour
 * and font but NOT a background — otherwise every custom container
 * (model_w / ai_lang_w / ai_toggle_w / method_w in Settings, etc.)
 * filled itself with bg colour and read as a darker rectangle on top
 * of its parent panel. */
QMainWindow {{
    background-color: {bg};
}}
QMainWindow, QWidget {{
    color: {text};
    font-family: "{body_font}";
    font-size: 13px;
}}

/* ─── Scrollbars ─── */
QScrollBar:vertical {{
    background: {bg_deep};
    width: 8px;
    border: none;
}}
QScrollBar::handle:vertical {{
    background: {border_alt};
    min-height: 40px;
    border-radius: 0px;
}}
QScrollBar::handle:vertical:hover {{
    background: {text_faint};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: none;
}}
QScrollBar:horizontal {{
    background: {bg_deep};
    height: 8px;
    border: none;
}}
QScrollBar::handle:horizontal {{
    background: {border_alt};
    min-width: 40px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {text_faint};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0px;
}}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    background: none;
}}

/* ─── Panels ─── */
QFrame[frameShape="1"] {{
    border: 1px solid {border};
}}
QFrame#Panel {{
    background-color: {panel};
    border: 1px solid {border_alt};
    border-radius: 2px;
}}
QFrame#PanelAlt {{
    background-color: {panel_alt};
    border: 1px solid {border_alt};
    border-radius: 2px;
}}

/* ─── Labels — typography hierarchy ─── */
QLabel {{
    background: transparent;
    border: none;
    color: {text};
    font-size: 13px;
    font-weight: 500;
}}
QLabel#Dim {{
    color: {text_dim};
    font-size: 12px;
    font-weight: 500;
}}
QLabel#Muted {{
    color: {text_faint};
    font-size: 11px;
    font-weight: 500;
}}
QLabel#Accent {{
    color: {accent};
}}
QLabel#SectionHeader {{
    color: {text_dim};
    font-family: "{header_font}", "JetBrains Mono";
    font-size: 10px;
    letter-spacing: 2px;
    padding: 4px 0px;
}}
QLabel#Eyebrow {{
    color: {text_faint};
    font-family: "{header_font}", "JetBrains Mono";
    font-size: 10px;
    letter-spacing: 3px;
    padding: 2px 0px;
}}
QLabel#BigNumber {{
    font-family: "{mono_font}";
    font-size: 28px;
    font-weight: 300;
    color: {text};
}}
QLabel#Wordmark {{
    font-family: "{header_font}", "JetBrains Mono";
    font-size: 14px;
    color: {text};
    letter-spacing: 5px;
}}

/* ─── Buttons ─── */
QPushButton {{
    background-color: {panel_alt};
    color: {text};
    border: 1px solid {border_alt};
    padding: 7px 14px;
    font-family: "{body_font}";
    font-size: 12px;
    font-weight: 500;
    min-height: 28px;
    border-radius: 2px;
}}
QPushButton:hover {{
    background-color: {panel_hover};
    border-color: {border_hover};
}}
QPushButton:pressed {{
    background-color: {accent_soft};
}}
QPushButton:disabled {{
    color: {text_faint};
    border-color: {border};
    background-color: {bg_deep};
}}
QPushButton#Primary {{
    background-color: {accent};
    color: {on_accent};
    border: 1px solid {accent};
    font-weight: 600;
    border-radius: 2px;
}}
QPushButton#Primary:hover {{
    background-color: {accent_hover};
    border-color: {accent_hover};
}}
QPushButton#Primary:pressed {{
    background-color: {accent_hover};
    border-color: {accent_hover};
}}
QPushButton#Primary:focus {{
    border-color: {text};
}}
QPushButton#Primary:disabled {{
    background-color: {accent_soft};
    color: {text_dim};
    border-color: {border_alt};
}}
QPushButton#Danger {{
    background-color: {risk};
    color: {on_accent};
    border: 1px solid {risk};
    font-weight: 600;
    border-radius: 2px;
}}
QPushButton#Danger:hover {{
    border-color: {text};
}}
QPushButton#Danger:pressed {{
    background-color: {risk_soft};
    color: {risk};
}}
QPushButton#Danger:focus {{
    border-color: {text};
}}
QPushButton#Ghost {{
    background-color: {panel_alt};
    border: 1px solid {border_alt};
    color: {text};
    padding: 7px 14px;
    border-radius: 2px;
}}
QPushButton#Ghost:hover {{
    background-color: {tint_bg};
    border-color: {border_hover};
}}
QPushButton#Ghost:disabled {{
    color: {text_faint};
    border-color: {border};
    background: transparent;
}}
QPushButton#Subtle {{
    background-color: {panel_alt};
    border: 1px solid {border_alt};
    color: {text_dim};
    padding: 7px 14px;
    border-radius: 2px;
}}
QPushButton#Subtle:hover {{
    color: {text};
    border-color: {border_hover};
    background-color: {tint_bg};
}}
QPushButton#SecondaryAction {{
    background-color: {panel_alt};
    border: 1px solid {border_alt};
    color: {text};
    padding: 7px 14px;
    border-radius: 2px;
}}
QPushButton#SecondaryAction:hover {{
    background-color: {panel_hover};
    border-color: {border_hover};
}}
QPushButton#SecondaryAction:pressed {{
    background-color: {tint_bg};
    border-color: {border_hover};
}}
QPushButton#SecondaryAction:focus {{
    border-color: {accent};
}}
QPushButton#SecondaryAction:disabled {{
    background-color: {bg_deep};
    color: {text_faint};
    border-color: {border};
}}
QPushButton#LinkButton {{
    background: transparent;
    color: {text_dim};
    border: none;
    border-bottom: 1px solid transparent;
    padding: 0px 0px 1px 0px;
    min-height: 0px;
    font-size: 11px;
    font-weight: 500;
    text-align: left;
}}
QPushButton#LinkButton:hover {{
    color: {accent};
    border-bottom: 1px solid {accent};
}}
QPushButton#LinkButton:pressed {{
    color: {accent_hover};
    border-bottom: 1px solid {accent_hover};
}}
QPushButton#LinkButton:focus {{
    color: {text};
    border-bottom: 1px solid {accent};
}}

/* ─── Sidebar ─── */
QFrame#Sidebar {{
    background-color: {bg_deep};
    border-right: 1px solid {border};
}}
QPushButton#SidebarBtn {{
    background: transparent;
    border: none;
    border-left: 2px solid transparent;
    color: {text_dim};
    text-align: left;
    padding: 7px 14px 7px 16px;
    font-size: 13px;
    font-family: "{body_font}";
    border-radius: 0px;
}}
QPushButton#SidebarBtn:hover {{
    background-color: {panel_alt};
    border-left: 2px solid {border_hover};
    color: {text};
}}
QPushButton#SidebarBtnActive {{
    background-color: {accent_soft};
    border: none;
    border-left: 2px solid {accent};
    color: {text};
    text-align: left;
    padding: 7px 14px 7px 16px;
    font-size: 13px;
    font-family: "{body_font}";
    border-radius: 0px;
}}

/* ─── Topbar ─── */
QFrame#Topbar {{
    background-color: {bg_deep};
    border-bottom: 1px solid {border};
}}
QFrame#SidebarDivider {{
    background-color: {border_alt};
    border: none;
}}

/* ─── Tables ─── */
QTableWidget, QTreeWidget, QTableView {{
    background-color: transparent;
    alternate-background-color: transparent;
    border: none;
    gridline-color: transparent;
    color: {text};
    font-family: "{mono_font}";
    font-size: 12px;
    selection-background-color: {accent_soft};
    selection-color: {text};
}}
QTableWidget::item, QTreeWidget::item, QTableView::item {{
    padding: 10px 10px;
    border-bottom: 1px solid {border};
}}
QTableWidget::item:hover, QTreeWidget::item:hover, QTableView::item:hover {{
    background-color: {panel_alt};
}}
QTableWidget::item:selected, QTreeWidget::item:selected, QTableView::item:selected {{
    background-color: {accent_soft};
    color: {text};
    border-bottom: 1px solid {border_alt};
}}
QTableWidget:focus, QTreeWidget:focus, QTableView:focus {{
    border: 1px solid {border_alt};
}}
QHeaderView::section {{
    background-color: {panel};
    color: {text_faint};
    border: none;
    border-bottom: 1px solid {border};
    border-right: none;
    padding: 10px 10px;
    font-family: "{mono_font}";
    font-size: 10px;
    font-weight: 500;
    letter-spacing: 2px;
}}

/* ─── Checkboxes — minimal tactical ─── */
QCheckBox {{
    spacing: 8px;
    color: {text};
    font-size: 12px;
}}
QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid {border_alt};
    background: {bg_deep};
    border-radius: 2px;
}}
QCheckBox::indicator:hover {{
    border-color: {border_hover};
    background: {panel_alt};
}}
QCheckBox::indicator:checked {{
    background: {accent_soft};
    border-color: {accent};
    image: none;  /* Qt draws default checkmark */
}}
QCheckBox::indicator:checked:hover {{
    background: {panel_hover};
    border-color: {accent_hover};
}}
QCheckBox::indicator:disabled {{
    border-color: {text_faint};
}}

/* ─── Combo Box ─── */
QComboBox {{
    background-color: {panel_alt};
    color: {text};
    border: 1px solid {border_alt};
    padding: 7px 28px 7px 10px;
    font-size: 12px;
    min-height: 28px;
    border-radius: 2px;
}}
QComboBox:hover {{
    border-color: {border_hover};
    background-color: {panel_hover};
}}
QComboBox:focus {{
    border-color: {accent};
}}
QComboBox::drop-down {{
    border: none;
    width: 24px;
    background: transparent;
}}
QComboBox::down-arrow {{
    image: none;
    width: 0px;
    height: 0px;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {text_dim};
    margin-right: 8px;
}}
QComboBox QAbstractItemView {{
    background-color: {panel};
    color: {text};
    border: 1px solid {border};
    selection-background-color: {accent_soft};
    selection-color: {text};
    padding: 2px;
}}

/* ─── Line Edit ─── */
QLineEdit {{
    background-color: {panel_alt};
    color: {text};
    border: 1px solid {border_alt};
    padding: 7px 10px;
    font-size: 12px;
    selection-background-color: {accent_soft};
    min-height: 28px;
    border-radius: 2px;
}}
QLineEdit:focus {{
    border-color: {accent};
}}

/* ─── Progress Bar ─── */
QProgressBar {{
    background-color: {bg_deep};
    border: 1px solid {border};
    height: 6px;
    text-align: center;
    color: {text_dim};
    font-family: "{mono_font}";
    font-size: 9px;
    border-radius: 0px;
}}
QProgressBar::chunk {{
    background-color: {accent};
    border-radius: 0px;
}}

/* ─── Tab Widget ─── */
QTabWidget::pane {{
    border: 1px solid {border};
    background-color: {bg};
    padding: 4px;
}}
QTabBar::tab {{
    background-color: transparent;
    color: {text_dim};
    border: none;
    border-bottom: 2px solid transparent;
    padding: 9px 14px;
    font-size: 12px;
    font-family: "{body_font}";
    font-weight: 500;
}}
QTabBar::tab:selected {{
    color: {text};
    border-bottom: 2px solid {accent};
}}
QTabBar::tab:hover:!selected {{
    color: {text};
}}

/* ─── Splitter ─── */
QSplitter::handle {{
    background-color: {border};
}}
QSplitter::handle:horizontal {{
    width: 1px;
}}
QSplitter::handle:vertical {{
    height: 1px;
}}

/* ─── Disabled state ─── */
QPushButton#Subtle:disabled {{
    color: {text_faint};
    border-color: {border};
    background: transparent;
}}

/* ─── Tooltip ─── */
QToolTip {{
    background-color: {panel_hover};
    color: {text};
    border: 1px solid {border_hover};
    padding: 6px 8px;
    font-size: 12px;
    border-radius: 2px;
}}

/* ─── Slider — industrial ─── */
QSlider {{
    background: transparent;
    min-height: 20px;
}}
QSlider::groove:horizontal {{
    border: 1px solid {border};
    height: 4px;
    background: {bg_deep};
    border-radius: 0px;
    margin: 0px;
}}
QSlider::handle:horizontal {{
    background: {accent};
    border: 1px solid {accent};
    width: 10px;
    height: 14px;
    margin: -6px 0;
    border-radius: 0px;
}}
QSlider::handle:horizontal:hover {{
    background: {accent_hover};
    border-color: {accent_hover};
}}
QSlider::sub-page:horizontal {{
    background: {accent_hover};
    border: 1px solid {border};
    border-radius: 0px;
}}

/* ─── Status Bar area ─── */
QFrame#StatusBlock {{
    background-color: {bg_deep};
    border-top: 1px solid {border_alt};
}}

/* ─── Checked chip / toggle button ─── */
QPushButton#Subtle:checked {{
    background-color: {accent_soft};
    color: {text};
    border-color: {accent};
}}
QPushButton#Subtle:checked:hover {{
    background-color: {accent};
    color: {on_accent};
}}

/* ─── Focus indicators (subtle) ─── */
QPushButton:focus {{
    border-color: {border_hover};
}}
QLineEdit:focus, QComboBox:focus {{
    border-color: {accent};
}}
QCheckBox:focus {{
    outline: none;
}}

/* ─── Text Edit / Console Feed ─── */
QTextEdit {{
    background-color: {bg_deep};
    color: {text_dim};
    border: 1px solid {border};
    padding: 12px 14px;
    font-size: 11px;
    font-family: "{mono_font}";
    selection-background-color: {accent_soft};
    line-height: 155%;
}}
QTextEdit:focus {{
    border-color: {accent};
}}

/* ─── Scroll area transparent ─── */
QScrollArea {{
    background: transparent;
    border: none;
}}
QScrollArea > QWidget > QWidget {{
    background: transparent;
}}

/* ─── Group Box ─── */
QGroupBox {{
    border: 1px solid {border};
    margin-top: 10px;
    padding-top: 14px;
    color: {text_dim};
    font-size: 12px;
    border-radius: 2px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    padding: 0 8px;
    color: {text_faint};
    font-family: "{header_font}", "JetBrains Mono";
    font-size: 10px;
    letter-spacing: 2px;
}}

/* ─── Settings inner nav rail ─── */
QFrame#SettingsRail {{
    background-color: {bg_deep};
    border-right: 1px solid {border};
}}
"""
