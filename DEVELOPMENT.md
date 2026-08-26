# Podbye — System Guardian

AI-assisted system analysis and cleanup console.

Podbye is a retro-futuristic **desktop application** focused on:

- ✅ intelligent storage analysis with **Semantic Pipeline**
- ✅ **container-first detection** — monolith roots claimed before content analysis; no "photo collection inside QGIS"
- ✅ **streaming entity detection** — detects meaning as files arrive
- ✅ **hierarchical media organization** — Images/Videos/Audio sub-categories
- ✅ **install verification** — distinguishes installed apps from orphaned files
- ✅ **smart AI selection** — only analyzes ambiguous entities
- ✅ **duplicate detection** — SHA-256/BLAKE3 hashing of large files, groups shown in dedicated Duplicates category
- ✅ **age-based heuristics** — entities not modified in 2+ years get reclaimable score boost; 5+ years = strong cleanup candidate
- ✅ transparent risk assessment (Safe / Review / Risk / Protected)
- ✅ local AI-assisted explanations via Ollama
- ✅ session persistence and resume
- ✅ safe cleanup with **Recycle Bin recovery** — select items, confirm in modal, items go to Windows Recycle Bin (recoverable)

📖 **[Semantic Pipeline Architecture](SEMANTIC_PIPELINE.md)** — Next-generation scanning system documentation

Unlike traditional "one-click cleaners", Podbye is designed as a system awareness tool:
it explains what files are, why they exist, and whether removing them is safe.

## Status

**Phases 0–3 complete.** Real scanning, Smart Scan entity detection (container-first two-phase pipeline), Semantic Pipeline (streaming detection, install verification, storage dashboard), AI pipeline, session persistence, performance stabilization, and Cleanup Engine with Recycle Bin all working.

**Phase 4 partial** — History screen (cleanup audit log, DEC-001), Startup Analysis, and Quick Cleanup all implemented. Smart Recommendations and Scheduled Scanning not yet built.

Current state (May 2026):
- ✅ **Semantic Pipeline** — next-generation storage intelligence
  - ✅ Streaming entity detection (real-time, not post-scan)
  - ✅ Hierarchical media structure (Images/Videos/Audio)
  - ✅ Install verification (registry + library manifests)
  - ✅ Smart AI queue (only ambiguous entities analyzed)
  - ✅ Orphaned application detection (safe cleanup targets)
  - ✅ **Semantic Storage Dashboard** — stock-market heatmap style treemap
    - Proportional block sizing (larger categories visually dominate)
    - Adaptive text contrast (automatic black/white based on background)
    - Responsive layout (1-4 columns based on window width)
    - Drill-down navigation (category → entities → files)
    - AI status visibility per category
    - Viewed state tracking
    - Debounced refresh (prevents UI freeze during scan)
    - "Start Analysis" button navigates to Analyze screen
- ✅ Full recursive filesystem scanning (background thread, batched)
- ✅ Smart Scan mode with two-phase container-first entity detection (Phase 1 Discovery → Phase 2 Assignment)
- ✅ AI explanation queue with Ollama integration
- ✅ **AI retry** — "Retry failed" button in Analyze and Startups feed headers; requeues only failed explanations
- ✅ Session save/restore/resume + multi-session history (history.json + per-session files)
- ✅ Protected path detection (system-critical paths never marked Safe)
- ✅ Performance optimization (indexed lookups, throttled UI, batched updates)
- ✅ 4 themes (Forest, Amber, Mono, Paper) — theme-aware colors across all screens
- ✅ **History screen** (DEC-001) — primary: cleanup audit log (WHEN / MODE / FREED / ITEMS, per-item expand); secondary: condensed scan sessions list
- ✅ **Startup Analysis** — real Windows detection (registry Run keys + startup folders + StartupApproved), risk classification, AI explanations
- ✅ **Cleanup Engine** — `CleanupConfirmDialog` modal, `SHFileOperationW` Recycle Bin, per-path error isolation, protected paths enforced at delete time
- ✅ **Permanent delete setting** — Settings → Scan → File Handling; off by default with warning label
- ✅ **Quick Cleanup** — five real scanners (User Temp, Browser Cache, Thumbnail Cache, Windows Update Cache, Windows Temp); one-click Recycle Bin cleanup; results shown inline
- ✅ **Duplicate Detection** — background SHA-256 hashing of files ≥ 10 MB, duplicate groups shown in Findings dashboard
- ✅ **Age-Based Heuristics** — age column in CategoryDetailView, stale entities marked with reclaimable score boost

## Running

**Requirements:** Python 3.8+ and PySide6

```
pip install PySide6>=6.4.3
py app/main.py
```

Or double-click `run.bat` on Windows.

**Optional:** Install [Ollama](https://ollama.ai) for local AI explanations.

## Project Structure

```
app/
  main.py               — entry point + QMainWindow shell + session wiring
  screens/
    home.py                 — session-aware dashboard (empty/live/resume states)
    quick_cleanup.py        — fast confidence-based safe cleanup (real detection via QuickCleanupDetector)
    analyze.py              — folder analysis with pipeline view + stop/resume
    findings_dashboard.py   — canonical Findings screen: donut overview + category detail + cleanup
    findings.py             — FindingDetail widget (detail subview, not a screen)
    startups.py             — real startup analysis (registry + startup folders + AI explanations)
    history.py              — cleanup audit log (primary) + condensed scan sessions (secondary); DEC-001
    settings.py             — tabbed settings (General/AI/Scan/Interface/About)
  widgets/
    sidebar.py          — navigation sidebar with brand + status
    topbar.py           — screen title bar
    panels.py           — Panel, StatCard, InfoRow, SectionHeader
    pills.py            — Badge (safe/review/risk/protected), Chip
    chips.py            — ChipBar filter rows
    progress.py         — StageProgress pipeline indicator
    tables.py           — table creation helpers
    feeds.py            — OperatorFeed (QPlainTextEdit-based, batched)
  services/
    scanner.py                    — ScanWorker (QThread, recursive os.walk, batched)
    entity_detector.py            — Smart entity detection (two-phase container-first, 9 passes)
    streaming_entity_detector.py  — Streaming real-time entity detection
    installed_software.py         — Install verification (registry/libraries)
    media_hierarchy.py            — Hierarchical media organization
    smart_ai_queue.py             — Intelligent AI selection
    ai_explainer.py               — AI queue (prioritized, concurrent, cached)
    ollama_client.py              — Ollama REST client (generate, list_models)
    prompt_builder.py             — Tone/length/language-aware prompt generation
    startup_detector.py           — Windows startup detection (registry Run keys + startup folders)
    cleanup_engine.py             — CleanupWorker, move_to_recycle_bin, permanent_delete, CleanupResult
    cloud_detector.py             — Cloud-sync root detection (OneDrive/Dropbox/Google Drive/iCloud/etc.)
    duplicate_detector.py         — DuplicateDetector (QThread, SHA-256/BLAKE3, files ≥ 10 MB)
    quick_cleanup_detector.py     — QuickCleanupDetector (QThread, five scanners)
  models/
    finding.py                — Finding dataclass + categorization + risk
    smart_entity.py           — SmartEntity dataclass (legacy batch detection)
    semantic_entity.py        — SemanticEntity with hierarchy + install verification
    startup_entry.py          — StartupEntry dataclass (name, command, risk, impact, AI fields)
    findings_table_model.py   — FindingsTableModel + FindingsFilterProxy + FindingsDelegate
  state/
    scan_state.py       — Global scan state (throttled signals, aggregation)
    session_store.py    — Session save/load/clear (last_run.json + history.json + per-session files)
  config/
    settings_store.py   — Persistent settings (config.json)
  themes/
    theme_manager.py    — palette definitions + QSS generator
  mock/
    sessions.py         — mock session/history data (legacy)
    findings.py         — mock findings data (legacy)
    startups.py         — mock startup entries
```

## Key Architecture

### Scan Pipeline
```
User selects folder → ScanWorker (background thread)
  → os.walk with symlink/junction safety
  → batched Finding emission (200 items/batch)
  → ScanState aggregation (throttled 400ms UI refresh)
  → Entity detection (background thread, indexed lookups)
  → AI explanation queue (concurrent, prioritized)
```

### Smart Scan Mode
Two-phase container-first detection:

**Phase 1 — Discovery:** Walks top 2–4 levels, claims known monolith roots (TeX Live, QGIS, MATLAB, Python, JetBrains, etc.) before any content analysis. Containment Rule: no file inside a claimed root is reclassified by later passes.

**Phase 2 — Assignment:** 9 heuristic passes run on the unclaimed pool only:
1. Known directory names (node_modules, .git, .venv, etc.)
2. Application markers (Steam, Ollama, Unity, Godot, etc.)
3. Browser profile detection
4. Cache keyword detection
5. **Protected/system path detection**
6. Content homogeneity analysis (media, archives, etc.) — untracked pool only
7. Recursive sub-folder grouping
8. Unclaimed top-level directories
9. Loose straggler files

### Risk Levels
| Level | Meaning |
|-------|---------|
| **Safe** | Auto-regenerated (caches, temp, logs) |
| **Review** | May contain user data (media, archives) |
| **Risk** | Actively used applications or data |
| **Protected** | System-critical — never remove |

### AI Explanation Queue
- Prioritized: Risk → Review → Unknown → Safe
- Protected items are **never** sent to AI
- Concurrent (configurable, default 3 workers)
- Emergency timeout (180s default)
- Disk-cached by path+size+mtime+model+tone+language
- Supports English and Ukrainian

## Design Direction

Podbye uses a **retro-futuristic "guardian terminal"** visual identity:

- Forest Terminal color palette (dark military greens)
- JetBrains Mono for all body/table text
- Silkscreen pixel font for branding, headings, badges
- No border-radius — sharp, structured panels
- Green = safe/active, Amber = caution, Red = risky/protected, Blue = system/info

## Core Principles

- transparency over automation
- readability over visual noise
- assistance over aggressive cleanup
- user control over hidden behavior
- safety over convenience (Protected paths cannot be cleaned)

## Development Workflow

- `ROADMAP.md` — project direction and phase tracking
- `Next Steps.md` — current priorities, limitations, and technical debt
- `DESIGN_RULES.md` — visual design token reference
- `QT_COMPONENT_SPEC.md` — Qt/QSS implementation spec
