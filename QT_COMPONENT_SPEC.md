# Podbye — Qt Component Spec

Maps the HTML/CSS design tokens to PySide6/QSS implementations.

---

## Palette Mapping

The design uses CSS custom properties. In Qt, we use Python dicts + `.format()` into QSS.

| CSS Token | Qt Palette Key | Usage |
|-----------|---------------|-------|
| `--bg` | `bg` | QMainWindow, QWidget, content area |
| `--bg-deep` | `bg_deep` | Sidebar, titlebar, feed, deep backgrounds |
| `--panel` | `panel` | QFrame#Panel, table header bg |
| `--panel-2` | `panel_alt` | Buttons, inputs, chips, secondary panels |
| `--panel-hi` | `panel_hover` | Button/chip hover state |
| `--border` | `border` | Primary 1px borders |
| `--border-2` | `border_alt` | Button/input/chip borders |
| `--border-hi` | `border_hover` | Hover/focus borders |
| `--text` | `text` | Primary text color |
| `--text-dim` | `text_dim` | Secondary/description text |
| `--text-faint` | `text_faint` | Muted, timestamps, nav icons |
| `--accent` | `accent` | Active states, checked, primary button bg |
| `--accent-2` | `accent_hover` | Primary button hover |
| `--accent-soft` | `accent_soft` | Active chip/nav bg, expanded row bg |
| `--safe` | `safe` | Safe status pill/dot |
| `--review` | `review` | Review status pill/dot |
| `--risk` | `risk` | Risk status pill/dot |
| `--safe-soft` | `safe_soft` | Safe pill background |
| `--review-soft` | `review_soft` | Review pill background |
| `--risk-soft` | `risk_soft` | Risk pill background |
| `--protected` | `risk` | Protected pill (shares risk color) |
| `--protected-soft` | `risk_soft` | Protected pill background |
| `--tint-bg` | `tint_bg` | Row hover, ghost button hover |

---

## Font Mapping

| Design Font | Qt Font | Variable |
|------------|---------|----------|
| IBM Plex Sans | Inter | `body_font` |
| IBM Plex Mono | JetBrains Mono | `mono_font` |
| Silkscreen | Silkscreen | `pixel_font` |

Note: We keep Inter/JetBrains Mono since they're already bundled and visually close
to IBM Plex Sans/Mono. Silkscreen is identical (same font).

---

## QSS Widget Mapping

### QMainWindow / QWidget (base)
```qss
background-color: {bg};
color: {text};
font-family: "{body_font}";
font-size: 13px;  /* was 14px, design uses 13px base */
```

### QFrame#Sidebar
```qss
background-color: {bg_deep};
border-right: 1px solid {border};
/* width: 196px — set in Python code */
```

### QPushButton#SidebarBtn
```qss
background: transparent;
border: none;
border-left: 2px solid transparent;
color: {text_dim};
text-align: left;
padding: 7px 14px 7px 16px;
font-size: 13px;
font-family: "{body_font}";
```

### QPushButton#SidebarBtnActive
```qss
background-color: {accent_soft};
border: none;
border-left: 2px solid {accent};
color: {text};
text-align: left;
padding: 7px 14px 7px 16px;
font-size: 13px;
font-family: "{body_font}";
```

### QFrame#Topbar
```qss
background-color: {bg};   /* was bg_alt, design uses --bg */
border-bottom: 1px solid {border};
/* height: 56px — set in Python */
/* padding: 0 22px — set in Python layout */
```

### QFrame#Panel
```qss
background-color: {panel};
border: 1px solid {border};
border-radius: 2px;
```

### QPushButton (default)
```qss
background-color: {panel_alt};
color: {text};
border: 1px solid {border_alt};
padding: 7px 14px;
font-family: "{body_font}";
font-size: 12px;
font-weight: 500;
min-height: 28px;
border-radius: 2px;
```

### QPushButton#Primary
```qss
background-color: {accent};
color: {bg_deep};
border: 1px solid {accent};
font-weight: 600;
border-radius: 2px;
```

### QPushButton#Primary:hover
```qss
background-color: {accent_hover};
border-color: {accent_hover};
```

### QPushButton#Ghost (was #Subtle)
```qss
background-color: transparent;
border: 1px solid {border};
color: {text};
border-radius: 2px;
```

### QPushButton#Ghost:hover
```qss
background-color: {tint_bg};
```

### QTableView (CategoryDetailView)

`CategoryDetailView` uses `QTableView` + model/proxy/delegate — no QTableWidget.

**Model stack:**
- `FindingsTableModel(QAbstractTableModel)` — source data; checkbox state in `_checked: set` (source rows) so proxy filtering never loses selections
- `FindingsFilterProxy(QSortFilterProxyModel)` — `filterAcceptsRow` (search text + risk chips); `lessThan` for 5-key custom sort (largest/smallest/risk/ai_analyzed/reclaimable)
- `FindingsDelegate(QStyledItemDelegate)` — paints risk badge rect (COL_RISK) with Silkscreen font; toggles `Qt.CheckStateRole` via `editorEvent` for COL_CHECK

```qss
QTableView {
background-color: transparent;
alternate-background-color: transparent;
border: none;
gridline-color: transparent;
selection-background-color: {accent_soft};
}
```

### QHeaderView::section
```qss
background-color: {panel};
color: {text_faint};
border: none;
border-bottom: 1px solid {border};
padding: 10px 10px;  /* min 10px top/bottom — text must not touch row border */
font-family: "{mono_font}";
font-size: 10px;
font-weight: 500;
letter-spacing: 2px;
text-transform: uppercase;
```

### QTableView::item
```qss
padding: 10px 10px;  /* min 10px top/bottom — text must not touch row border */
border-bottom: 1px solid {border};
```

### QCheckBox::indicator
```qss
width: 14px;
height: 14px;
border: 1px solid {border_alt};
background: {panel_alt};
border-radius: 1px;
```

### QCheckBox::indicator:checked
```qss
background: {accent};
border-color: {accent};
```

### QComboBox
```qss
background-color: {panel_alt};
color: {text};
border: 1px solid {border_alt};
padding: 7px 10px;
font-size: 12px;
border-radius: 2px;
min-height: 28px;
```

### QLineEdit
```qss
background-color: {panel_alt};
color: {text};
border: 1px solid {border_alt};
padding: 7px 10px;
font-size: 12px;
border-radius: 2px;
min-height: 28px;
```

### QLineEdit:focus, QComboBox:focus
```qss
border-color: {accent};
```

### QProgressBar
```qss
height: 6px;  /* was 8px */
background-color: {bg_deep};
border: 1px solid {border};
border-radius: 0px;
```

### QProgressBar::chunk
```qss
background-color: {accent};
```

### QSlider::groove:horizontal
```qss
border: 1px solid {border};
height: 4px;
background: {bg_deep};
```

### QSlider::handle:horizontal
```qss
background: {accent};
border: 1px solid {accent};
width: 10px;
height: 14px;
margin: -6px 0;
```

### QScrollBar:vertical
```qss
background: {bg_deep};
width: 8px;  /* slimmer */
border: none;
```

### QScrollBar::handle:vertical
```qss
background: {border_alt};
min-height: 40px;
border-radius: 0px;
```

---

## Theme Signal Pattern

`theme_manager.py` exposes a singleton `_ThemeSignaller(QObject)` with a `theme_changed = Signal(str)` signal emitted on every `build_qss()` call. Any screen that bakes palette colors into inline `setStyleSheet` calls must subscribe and re-apply those styles.

**Subscription (in screen `__init__` after `_build_ui`):**
```python
from app.themes.theme_manager import theme_signaller
theme_signaller().theme_changed.connect(self._rebuild_styles)
```

**Implementation (`_rebuild_styles`):**
- `HomeScreen` — calls `self.refresh()` (full dynamic area rebuild)
- `AnalyzeScreen` — re-applies `_bar_qss()` to progress bars + label styles; calls `chip._apply(chip._state)` for each pipeline chip
- `StartupsScreen` — calls `_show_results()` or `_show_idle()` depending on whether entries are loaded
- `QuickCleanupScreen` — updates saved separator and checkmark label references

**Rule:** Widgets that use `changeEvent(QEvent.StyleChange)` (e.g., `CategoryCardWidget`, `LoadingStateWidget`) update automatically — no subscription needed. Only screens with direct `setStyleSheet(f"...{palette_value}...")` calls need `_rebuild_styles`.

---

## Custom Widget Specs (Python-painted)

### StatusPill (Badge)
- QLabel subclass
- Mono font 10px, uppercase, 0.06em spacing
- Padding: 3px 8px
- Border: 1px, radius 2px
- Dot: 5×5px painted before text
- Color variants: safe/review/risk/protected using palette keys
- Protected variant uses risk colors (same visual weight)
- Additional variants: completed, running, idle, info, locked, partial_halted

### FilterChip
- QPushButton, checkable
- 11px/500 font, 4px 9px padding, 2px radius
- Unchecked: `panel_alt` bg, `border_alt` border, `text_dim` color
- Checked: `accent_soft` bg, `accent` border, `text` color
- Optional dot swatch: 6×6px, painted via stylesheet or QIcon
- Count badge: mono 10px, faint (or accent when checked)

### ToggleSwitch
- Custom QWidget, 32×18px
- Painted track + knob
- ON: accent track, bg_deep knob
- OFF: panel_alt track, text_faint knob
- Adjacent "ON"/"OFF" label: mono 10px faint

### SegmentedControl
- QWidget with horizontal QPushButtons
- 1px border-2 outer border
- Active segment: accent_soft bg, text color, 1px top accent border
- Inactive: text_dim, transparent bg
- Mono 11px, 6px 12px padding

### TimelineBar (History)
- Custom QWidget, 64px height
- 30 vertical bars, flex: 1 each, 4px gap
- Active day: accent color, full opacity
- Inactive: panel_alt, 0.5 opacity, border only

### DotLed
- 6×6px inline indicator
- Color: accent (or safe/review/risk)
- Box-shadow / glow effect optional in Qt

---

## Spacing Constants (Python)

```python
SPACING = {
    'xs': 4,
    'sm': 6,
    'md': 8,
    'lg': 10,
    'xl': 12,
    '2xl': 14,
    '3xl': 16,
    '4xl': 18,
    '5xl': 22,
}

CONTENT_PADDING = 22
PANEL_PADDING = 14
PANEL_HEADER_PADDING = (11, 14)
PANEL_GAP = 16
SIDEBAR_WIDTH = 196
TOPBAR_HEIGHT = 56
TITLEBAR_HEIGHT = 32
SETTINGS_RAIL_WIDTH = 220
SETTING_LABEL_WIDTH = 220
```

---

## Screen-Specific Notes

### Settings
- Replace top tabs with inner left nav rail (220px, bg_deep)
- 5 sections: General, AI, Scan, Interface, About
- Each section has multiple grouped panels
- Section header: pixel 14px + "// subtitle" in mono faint 11px
- SettingRow: label col 220px + content col flex + optional hint col 110px

### Findings
- Topbar right: search input (240px) + Export ghost btn + Clean primary btn
- Filter chips with eyebrow label, category chips, separator, risk chips (Safe/Review/Risky/Protected), separator, size/age chips, AI status chips
- Table inside panel with panel header (title + sort info)
- Footer with item count + selection summary
- Cleanup preview panel (hidden by default): risk breakdown, size summary, Protected/Risk warnings, confirm/cancel buttons
- Categories: Cache & Temp, Media, Archives, Browser Data, Duplicates, Dev Artifacts, System Logs, Unknown, Applications, AI / ML, Documents, System

### Analyze
- Pipeline stage chips across top (mode-dependent: Smart has Entity detection + AI classification stages)
- Progress bar (6px) with percentage
- Split: partial findings table (left) + operator feed (right)
- Start/Stop toggle button (transforms during scan)
- AI telemetry: active/pending/failed/done counters
- Elapsed time display
- Scan mode selector (Smart/All files)

### History
- Timeline strip panel at top
- Sessions table panel below with expand/collapse rows
- Expanded row shows: scanned paths, explanation mode, distribution, action buttons
