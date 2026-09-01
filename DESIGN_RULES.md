# Podbye — Design Rules

Design-system reference for the current PySide6 UI.

---

## Typography

| Role | Family | Size | Weight | Spacing | Usage |
|------|--------|------|--------|---------|-------|
| Body | IBM Plex Sans (Inter in Qt) | 13px base | 400/500 | 0.005em | UI text, labels, descriptions |
| Mono | IBM Plex Mono (JetBrains Mono in Qt) | 11.5–13px | 400 | tnum, ss01 | Tables, paths, metrics, feeds, timestamps |
| Pixel | Silkscreen | 9–11px | 400 | 0.06–0.18em | Section headers (uppercase), badges, wordmark, eyebrows |

### Hierarchy
- **64px mono light** — hero big numbers
- **48px mono light** — secondary big numbers
- **36px mono light** — stat card numbers
- **14px pixel, 0.14em spacing** — screen crumb (topbar)
- **13px body 500** — primary UI text, nav items
- **12.5px body** — setting row labels, table cells
- **12px body** — secondary text
- **11.5px mono** — paths, timestamps, connections
- **11px body 500** — chips, small buttons
- **10px pixel 0.18em** — eyebrow labels (uppercase)
- **10px mono** — keyboard shortcuts, footer info, faint metadata
- **9px pixel** — build version in sidebar

---

## Color Tokens (per theme)

### Forest (default)
| Token | Value | Usage |
|-------|-------|-------|
| `--bg` | `#0e1612` | App background |
| `--bg-deep` | `#080d0a` | Sidebar, titlebar, feed, deep background |
| `--panel` | `#141d18` | Panel/card background |
| `--panel-2` | `#18241e` | Buttons, inputs, chips (secondary panel) |
| `--panel-hi` | `#1d2c25` | Hover state for buttons |
| `--border` | `#213028` | Primary borders |
| `--border-2` | `#2b3d33` | Secondary borders (buttons, inputs, chips) |
| `--border-hi` | `#3a5648` | Hover/focus border |
| `--text` | `#d6e2da` | Primary text |
| `--text-dim` | `#8a9b8f` | Secondary text, descriptions |
| `--text-faint` | `#57685e` | Muted, timestamps, nav icons |
| `--accent` | `#7cc596` | Active indicators, accents, checked state |
| `--accent-2` | `#4d8e63` | Hover on primary buttons |
| `--accent-soft` | `#1b2e22` | Active chip/nav background |
| `--safe` | `#7cc596` | Safe status |
| `--review` | `#d8b46a` | Review status |
| `--risk` | `#d68a78` | Risk status |
| `--protected` | `#d68a78` | Protected status (shares risk color) |
| `--tint-bg` | `rgba(124,197,150,0.04)` | Hover tint |

### Amber
| Token | Value |
|-------|-------|
| `--bg` | `#14100a` |
| `--bg-deep` | `#0c0905` |
| `--panel` | `#1c160c` |
| `--panel-2` | `#221b10` |
| `--panel-hi` | `#2a2114` |
| `--border` | `#2c2316` |
| `--border-2` | `#3a2f1d` |
| `--border-hi` | `#524325` |
| `--text` | `#ecdcb8` |
| `--text-dim` | `#a08c69` |
| `--text-faint` | `#645540` |
| `--accent` | `#e8b169` |
| `--accent-2` | `#b07f3a` |
| `--accent-soft` | `#2a2014` |
| `--safe` | `#b9c66e` |
| `--review` | `#e8b169` |
| `--risk` | `#d27a5c` |

### Mono Ink
| Token | Value |
|-------|-------|
| `--bg` | `#0a0a0a` |
| `--bg-deep` | `#050505` |
| `--panel` | `#121212` |
| `--panel-2` | `#181818` |
| `--panel-hi` | `#1f1f1f` |
| `--border` | `#262626` |
| `--border-2` | `#353535` |
| `--border-hi` | `#4d4d4d` |
| `--text` | `#ededed` |
| `--text-dim` | `#8c8c8c` |
| `--text-faint` | `#5a5a5a` |
| `--accent` | `#ffffff` |
| `--accent-2` | `#b3b3b3` |
| `--accent-soft` | `#1c1c1c` |

### Paper (light)
| Token | Value |
|-------|-------|
| `--bg` | `#f1ece1` |
| `--bg-deep` | `#e6dfd0` |
| `--panel` | `#faf6ec` |
| `--panel-2` | `#efe9da` |
| `--panel-hi` | `#e6dfcd` |
| `--border` | `#d4ccb8` |
| `--border-2` | `#b8ad94` |
| `--border-hi` | `#94896f` |
| `--text` | `#1c201d` |
| `--text-dim` | `#5e6358` |
| `--text-faint` | `#8c8d80` |
| `--accent` | `#3d6b48` |
| `--accent-2` | `#6c8b6f` |
| `--accent-soft` | `#e3ddc9` |

---

## Layout Rules

### App Shell
- **Titlebar**: 32px height, `--bg-deep`, 11px font, centered title (pixel font)
- **Sidebar**: 196px fixed width, `--bg-deep`, 1px right border
- **Topbar**: 56px height, `--bg` background, 1px bottom border, 22px horizontal padding
- **Content area**: flex: 1, 22px padding, overflow auto

### Sidebar
- Brand block: 6px 14px 16px padding, bottom border, 10px gap
- Nav section label: mono 9px, 0.18em spacing, uppercase, `--text-faint`, padding 14px 16px 6px
- Nav item: 7px 14px 7px 16px padding, 13px font, 10px gap, 2px left border (transparent or accent)
- Active nav item: `--accent-soft` bg, `--accent` left border, `--text` color
- Footer: auto margin-top, 12px 14px padding, top border, mono 10px

### Topbar
- Crumb: pixel font 13px, 0.14em spacing, uppercase
- Sub: mono 11px, `--text-faint`

### Content Padding
- Standard: 22px all sides
- Between panels: 16px gap

---

## Component Styles

### Panel
- Background: `--panel`
- Border: 1px solid `--border`
- Border-radius: 2px
- Header: 11px 14px padding, 1px bottom border, pixel font 11px 0.14em for title
- Body: 14px padding

### Buttons
- **Default**: `--panel-2` bg, 1px `--border-2`, 12px/500 font, 7px 14px padding, 2px radius
- **Primary**: `--accent` bg + border, `--bg-deep` text color, 600 weight
- **Ghost**: transparent bg, 1px `--border`, hover → `--tint-bg`
- Hover: `--panel-hi` bg, `--border-hi` border
- Gap between buttons: 8px

### Chips
- `--panel-2` bg, 1px `--border-2`, 11px/500 font, 4px 9px padding, 2px radius
- Active ("on"): `--accent-soft` bg, 1px `--accent` border, `--text` color
- Dot swatch: 6px × 6px, 1px radius
- Count suffix: mono 10px, `--text-faint` (or `--accent` when active)
- Gap between chips: 6–8px

### Pills (status badges)
- Mono 10px, 0.06em spacing, uppercase
- 3px 8px 3px 7px padding, 1px border, 2px radius
- 5px × 5px dot before text (currentColor)
- `.safe`: color + border = `--safe`, bg = `--safe-soft`
- `.review`: color + border = `--review`, bg = `--review-soft`
- `.risk`: color + border = `--risk`, bg = `--risk-soft`
- `.protected`: color + border = `--risk`, bg = `--risk-soft` (same visual as risk)

### Checkboxes
- 14px × 14px, 1px `--border-2`, `--panel-2` bg, 1px radius
- Checked: `--accent` bg + border, checkmark in `--bg-deep` color

### Toggle (custom)
- 32px × 18px track, 2px radius
- ON: `--accent` track, knob at right (14px × 14px, `--bg-deep`)
- OFF: `--panel-2` track with `--border-2` border, knob left (`--text-faint`)
- Adjacent label: mono 10px, faint, "ON"/"OFF"

### Tables
- 12.5px base font size
- Header: mono 10px, 0.14em spacing, uppercase, `--text-faint`, **10px 10px padding** (min — text must not touch row border), sticky
- Cell: **10px 10px padding** (min), 1px bottom border
- Path cells: mono 11.5px, `--text-dim`
- Size cells: mono, tnum, right-aligned, nowrap
- Hover row: `--tint-bg`
- Expanded row: `--accent-soft` background
- Sort indicator: mono 10px faint, "SORT: SIZE ▾"

### Form Inputs
- `--panel-2` bg, 1px `--border-2`, 7px 10px padding, 12px font, 2px radius
- Focus: `--accent` border
- Max-width: 320px (default), varies per context

### Progress Bar
- 6px height, `--bg-deep` bg, 1px `--border`
- Fill: `--accent`

### Segmented Control
- Inline-flex row, 1px `--border-2` border, `--panel-2` bg
- Active segment: `--accent-soft` bg, `--text` color, 1px top `--accent` border
- Inactive: `--text-dim`, transparent bg
- Mono 11px, 6px 12px padding

### Slider (Range)
- 200px default width
- Adjacent value label: mono 12px, right-aligned, 64px width

---

## Spacing Scale
- 4px, 6px, 8px, 10px, 12px, 14px, 16px, 18px, 22px
- Component internal padding: 14px
- Panel header padding: 11px 14px
- Content area padding: 22px
- Between-section gap: 16px
- Between panels in same section: 16px

---

## Settings Layout (expanded design)
- Inner left nav rail: 220px, `--bg-deep`, top padding 18px
- Nav items same style as sidebar but with 8px 18px padding
- Right pane: flex 1, 22px 24px padding
- Section title: pixel 14px, 0.14em, with "// subtitle" in mono faint 11px
- Setting rows: 18px gap, 10px vertical padding, dashed bottom border
- Setting label column: 220px fixed, 12.5px font
- Setting description: 11px, `--text-dim`, 1.5 line-height
- Between panels within a section: 16px gap

---

## Findings Layout
- Filter chips row: 8px gap, flex-wrap, 14px margin-bottom
- Eyebrow "Categories" label before chips
- Separator: 1px × 16px `--border`, 0–4px margin
- Table colgroup: checkbox 28px, expand 28px, category 130px, path flex, size 90px, age 60px, risk 110px, conf 80px
- Footer: 10px 14px padding, top border, space-between, mono faint 10px

---

## History Layout
- Timeline strip: 30-day bar chart, 64px height, flex row with 4px gap
- Each day: flex 1, min-height 4px, border 1px
- Active days: `--accent` bg, full opacity
- Inactive days: `--panel-2` bg, 0.5 opacity
- Labels below: mono faint 10px, space-between ("30d ago" / "today")
- Sessions table: expand 28px, when 170px, target flex, duration 80px, model 130px, findings 100px, distribution 200px, cleaned 110px
