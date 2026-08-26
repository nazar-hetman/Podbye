# PODBYE Roadmap

## Vision

Podbye is a local-first system awareness console — not an aggressive cleaner.

The goal is helping users understand:
- what exists on their machine
- why it exists
- what is safe to remove
- what impacts performance or storage
- what should be reviewed manually

Podbye focuses on:
- transparency
- readability
- local AI assistance
- calm operator-style UX
- safe recommendations

---

# Phase 0 — Visual Prototype ✅ COMPLETE

**Status: Done — 2025-05-07**

Built a complete desktop UI prototype using Python + PySide6.

- ✅ QMainWindow with sidebar, topbar, status bar
- ✅ Keyboard-navigable screen switching
- ✅ Forest Terminal color palette + 3 alternate themes
- ✅ JetBrains Mono + Silkscreen pixel font typography
- ✅ Reusable Panel, StatCard, Badge, Chip components
- ✅ All 7 screens laid out (Home, Quick Cleanup, Analyze, Findings, Startups, History, Settings)

---

# Phase 1 — Core Scan Engine ✅ COMPLETE

**Status: Done — 2025-05-10**

## ✅ Filesystem Scanning
- Recursive `os.walk` in background `QThread`
- Symlink/junction detection and safe skipping
- Permission error handling with graceful fallback
- Batched emission (200 items/batch) to avoid UI flooding
- Progress reporting (count + current path)
- Scan rate logging (items/s)

## ✅ Smart Scan Mode (Entity Detection)
- Two-phase container-first entity detector (Phase 1 Discovery + Phase 2 Assignment)
- Phase 1 Discovery: claims known monolith roots by name pattern before any content analysis
- Phase 2 Assignment — 9-pass semantic classification of remaining items:
  - Pass 1: Known directory names (node_modules, .git, .venv, __pycache__, etc.)
  - Pass 2: Application markers (Steam, Ollama, Unity, Unreal, Godot, JetBrains, VS Code, etc.)
  - Pass 3: Browser profile detection (Chrome, Firefox, Edge, Brave, Opera, Vivaldi)
  - Pass 4: Cache/temp keyword detection
  - Pass 5: Protected/system path detection
  - Pass 6: Content homogeneity analysis (media, archives, databases, photogrammetry) — untracked pool only
  - Pass 7: Recursive sub-folder grouping
  - Pass 8: Unclaimed top-level directory fallback
  - Pass 9: Loose straggler file grouping
- Containment Rule: no content classification fires inside claimed monolith roots
- Indexed lookups (O(k) children gathering, not O(n) linear scan)
- Background thread execution (non-blocking)

## ✅ Category & Risk Classification
- Rule-based categorization: Cache & Temp, Media, Archives, Browser Data, Dev Artifacts, System Logs, Applications, AI/ML, Documents, System, Unknown
- 4-level risk system: Safe, Review, Risk, Protected
- Protected paths: Windows, System32, ProgramData, critical AppData dirs
- Risk reasons tracked and displayed

## ✅ Session Persistence
- Auto-save every 8 seconds during scan
- Save on app close
- Save on scan complete
- Restore findings, entities, target, mode from `last_run.json`
- Resume scan (skip already-known paths)
- Session ID for stale-result protection

## ✅ Stop/Resume Flow
- Start/Stop toggle button (single button transforms)
- Graceful halt preserves partial results
- Resume from Home screen with session details
- Scanner-level dedup via `skip_paths` parameter

## ✅ Performance Optimization
- ScanWorker batching (200 items/flush)
- Throttled UI refresh (400ms coalescing)
- OperatorFeed: QPlainTextEdit with max line cap
- Indexed entity detection (children_index dict)
- Findings dict cache (rebuilt only when dirty)
- Performance diagnostics: slow operations logged to operator feed
- setUpdatesEnabled wrapping on table population

---

# Phase 1.5 — Intelligence Layer ✅ COMPLETE

**Status: Done — 2025-05-10**

## ✅ AI Explanations (Ollama Integration)
- Local Ollama REST API client
- Model listing and auto-detection
- Connection testing (background, non-blocking)
- Endpoint validation (local-only by default)

## ✅ AI Explanation Queue
- Prioritized processing: Risk → Review → Unknown → Safe
- Protected items **never** sent to AI (auto-disabled)
- Configurable concurrency (default: 3 workers)
- Emergency timeout (default: 180s)
- Disk-cached results (path + size + mtime + model + tone + language + length)
- Queue telemetry signal: done, total, active, failed
- Stale-result protection via session_id

## ✅ AI Retry for Failed Explanations (2026-05-16)
- "Retry failed" button in Analyze screen operator feed header — visible when failures exist and queue is idle
- "Retry failed" button in Startups screen header — same visibility conditions
- Retries only failed items; resets `ai_status`/`ai_explanation`/`ai_error` fields before re-queueing
- Does not re-run successful or pending explanations

## ✅ Prompt Builder
- Tone-aware (neutral, concise, detailed)
- Length-aware (compact, standard, detailed)
- Language-aware (English, Ukrainian)
- Entity-specific prompts (grouped context, file samples)
- Path truncation for token efficiency

## ✅ AI Settings
- Endpoint configuration
- Model selection (with refresh)
- Tone and length control
- Language selection
- Enable/disable toggle
- "Explain risky only" option
- Timeout and concurrency configuration

---

# Phase 2 — Semantic Pipeline ✅ COMPLETE

**Status: Done — 2026-05-13**

## ✅ Streaming Entity Detection
- `StreamingEntityDetector` — real-time entity grouping (not post-scan batch)
- Every scanned item belongs to a semantic entity — no loose files
- Operator feed shows live progress per pass

## ✅ Hierarchical Media Organization
- `MediaHierarchyBuilder` — sub-categorizes media folders
- Sub-categories: Photos / Videos / Screenshots / RAW / Creative Projects
- Risk assignment per sub-type (Screenshots → Safe, Photos → Review)

## ✅ Install Verification
- `InstalledSoftwareValidator` — checks registry Uninstall keys, Steam ACF manifests, Epic `.item` files, GOG database, uninstaller presence
- Orphaned apps flagged as `application_orphaned` with lower risk

## ✅ Smart AI Queue
- `SmartAIQueue` — only analyzes ambiguous entities (ambiguity score > 0.6)
- Template summaries for well-known types (cache, venv, node_modules) — no AI call
- Priority calculation: ambiguity × risk × size × type adjustment

## ✅ Container-First Semantic Detection
- Two-phase detection pipeline in `entity_detector.py`
- Phase 1 Discovery: claims known monolith roots (TeX Live, QGIS, MATLAB, R, Python, JetBrains, etc.) before any content analysis runs
- Phase 2 Assignment: all existing passes run only on the unclaimed pool
- The Containment Rule enforced: no file inside a claimed root reclassified by content analysis
- Monolith list overridable via `settings/scan.monolith_patterns`
- Operator feed logs: `[smart] phase 1: discovery — found N entity roots` + `[smart] phase 2: assignment — claimed X files, Y untracked`

## ✅ Semantic Storage Dashboard
- `FindingsDashboard` is the canonical Findings screen — flat table removed from navigation
- Proportional block sizing (largest categories dominate)
- Drill-down: dashboard → category → entity table with selection + cleanup
- Adaptive text contrast, responsive 1–4 column layout
- AI status visibility per category block
- Debounced resize and render-hash caching for performance
- `FindingsScreen` (legacy flat table) deleted; `FindingDetail` kept as detail subwidget

## ✅ SemanticEntity Model
- `SemanticEntity` dataclass: hierarchy, install verification, AI status, reclaimable bytes, content breakdown
- Replaces `SmartEntity` as primary entity representation

---

# Phase 3 — Cleanup Safety Foundation ✅ COMPLETE

**Status: Done — 2026-05-15**

## ✅ Cleanup Engine
- `app/services/cleanup_engine.py` — `CleanupWorker(QThread)`, `CleanupResult`, `move_to_recycle_bin()`, `permanent_delete()`
- Uses Windows `SHFileOperationW` with `FOF_ALLOWUNDO` — items go to Recycle Bin, fully recoverable
- `ProtectedPathError` enforced at execution time (not just queue time) — double-checked on every delete
- Per-path error isolation — one locked file never aborts the batch
- Permanent delete gated behind `perm_delete_enabled` flag + no protected items in list

## ✅ Permanent Delete Setting UI (2026-05-16)
- Settings → Scan → File Handling panel exposes the `perm_delete_enabled` toggle
- Default OFF; orange warning label: "Permanent delete bypasses Recycle Bin and cannot be undone."
- Setting persisted in `settings_store.py`; read by `CleanupWorker` at delete time

## ✅ Confirmation Modal
- `app/screens/cleanup_dialog.py` — `CleanupConfirmDialog(QDialog)`
- Shows: total items, size, risk breakdown, per-item list for Risk/Review items
- Protected items displayed as excluded with "will be skipped" note
- Risk items require typing confirmation phrase ("delete N items") before action button activates
- Live progress indicator during operation (item name + count)
- Result summary after completion (succeeded / failed / protected skipped)
- Blocks close/escape during active operation

## ✅ Post-Cleanup State Update
- `ScanState.remove_entities_by_path(paths)` — removes entities, purges matching findings, emits `ui_refresh`
- Findings table rebuilds automatically via existing `ui_refresh` signal
- Toast message in selection bar: "✓ N items moved to Recycle Bin · X freed"
- Cleanup record written to `%APPDATA%/Podbye/sessions/cleanup_{timestamp}.json`

## ✅ Cleanup Preview Panel (infrastructure)
- Selected items risk breakdown (Protected / Risk / Review / Safe)
- Total size summary
- Warning display for Protected and Risk selections
- Cancel button to dismiss preview

## ✅ Protected Entity System
- Protected risk level in Finding model
- Protected risk level in SmartEntity model
- Protected badge variant (red styling)
- Protected filter chip in Findings screen
- System-critical paths automatically detected and protected
- Protected items excluded from AI queue
- Protected items show "Do not touch" recommendation

---

# Phase 4 — System Awareness 🟡 PARTIAL

## ✅ History Screen (DEC-001 — Reframed as Cleanup Audit Log)
- **Implemented 2026-05-16:** Primary content is cleanup audit log; condensed scan session list is secondary
- Cleanup table: WHEN / MODE badge (Recycle Bin / Permanent) / FREED / ITEMS columns; click-to-expand `CleanupRecordDetail`
- `CleanupRecordDetail`: summary stats row + scrollable per-item list (risk dot, name, category, size, ERR badge for failures); "Items are in Windows Recycle Bin and can be restored" note for recycle_bin mode
- Scan sessions table: condensed (no 30-day timeline strip); WHEN / TARGET / MODE / ITEMS columns
- `load_cleanup_records()` added to `session_store.py` — globs `cleanup_{timestamp}.json`, newest first
- Empty states per section; global empty state when no records at all

## ✅ Startup Analysis (Real)
- `StartupDetector` reads Windows registry Run keys (HKCU + HKLM + WOW64)
- Reads `StartupApproved` keys for accurate enabled/disabled state
- Reads User and All Users startup folders (`.lnk` shortcuts)
- `.lnk` resolution: `win32com` primary; binary MS-SHLLINK parser fallback (header validation → IDList skip → LinkInfo LocalBasePath ASCII + LocalBasePathOffsetUnicode UTF-16LE, all bounds-checked)
- If target still unresolved, shows `.lnk` path and marks "shortcut target unresolved" in risk_reason
- Risk classification: Protected (system components) → Risk (security/drivers) → Review (launchers/cloud) → Safe
- Publisher lookup via `VS_VERSIONINFO` exe resource
- Deduplicated + sorted by risk (Risk → Review → Protected → Safe, enabled first)
- AI explanation integration
- Podbye does **not** modify startup entries — recommendation only

## ⏳ Smart Recommendations
- Pattern-based contextual suggestions
- "Last cleaned X days ago" awareness

## ⏳ Scheduled Scanning
- Background analysis (idle-only, AC-only, weekly)
- Important: Podbye never auto-deletes without confirmation

## ✅ Partial Results Streaming
- Already implemented — findings stream to UI during scan
- Throttled at 400ms intervals

## ✅ Quick Cleanup (Real) — 2026-05-16
- `QuickCleanupDetector(QThread)` in `app/services/quick_cleanup_detector.py`
- Five scanners: User Temp, Browser Cache (Chrome/Edge/Brave/Firefox/Opera/Vivaldi), Thumbnail Cache, Windows Update Cache, Windows Temp
- Auto-scans on first show; Rescan button; categories appear live as each scanner finishes
- One-click Recycle Bin cleanup via `CleanupWorker`; progress and results shown inline (no modal)
- Post-cleanup item counts updated per row; cleanup record written to History

---

# Phase 5 — Future Concepts ⏳ PLANNED

## ✅ Duplicate Detection (2026-05-15)
- `DuplicateDetector(QThread)` in `app/services/duplicate_detector.py`
- Hashes files > 10 MB (configurable via `scan/dedup_threshold_mb`) using SHA-256 (BLAKE3 if installed)
- Groups identical files → emits one `SmartEntity(duplicate_group)` per group
- `dup_reclaimable` tracks bytes of all-but-newest copies
- Runs after entity detection completes; entities added live to `ScanState`
- Findings dashboard shows a dedicated "Duplicates" category

## ✅ Age-Based Heuristics (2026-05-15)
- `SmartEntity.age_boost`: 0.2 for 2y+ old, 0.4 for 5y+ old eligible entities
- Eligible types: dev_artifacts, installer_group, archive_group, build_folder, venv, node_modules, temp_folder, cache_folder, log_folder, ai_cache
- Age boost applies to reclaimable_bytes for Review-risk entities (partial credit)
- Recommendations updated: "Strong cleanup candidate — not modified in 5+ years"
- `_format_age` improved: "2y 3m", "4m", "13d" (was just "Nd")
- "Age" column added to CategoryDetailView (COL_AGE = 7), right-aligned, ResizeToContents
- "Oldest first" sort key added to FindingsFilterProxy

## ✅ Development Environment Awareness (Partial)
- Already detected: node_modules, .venv, __pycache__, .git, build folders, .cargo, .rustup
- Future: Docker leftovers, SDK caches

## ⏳ Media & Capture Cleanup
OBS recordings, screenshots, exports, temporary renders.

## ⏳ Local AI Memory
Optional knowledge system for personalized recommendations.

## ⏳ Advanced Visualization
Storage heatmaps, category trends, cleanup impact graphs.

## ✅ QAbstractTableModel Migration (2026-05-15)
`CategoryDetailView` migrated to `QTableView` + `FindingsTableModel` + `FindingsFilterProxy` + `FindingsDelegate`. Virtual scrolling active; no widget-per-cell overhead.

## ✅ Cloud-Sync Safety Detection (2026-05-15)
`app/services/cloud_detector.py` — path-based detection of OneDrive / Dropbox / Google Drive / iCloud / Box / MEGA roots under `%USERPROFILE%` + Windows reparse-point check. Entities in cloud paths get `cloud_sync_provider` set, risk forced to minimum Review, ☁ badge in the table, and a separate cloud acknowledgment checkbox in the cleanup modal.

---

# Design Principles

Podbye is intentionally designed to avoid:
- aggressive "one-click optimization"
- hidden cleanup behavior
- fake hacker aesthetics
- flashy RGB-heavy cyberpunk visuals

The product should feel like:
- a calm machine intelligence
- a tactical operator console
- a trustworthy local system assistant

---

# Current Status

**Phases 0, 1, 1.5, 2, and 3 complete.** Real scanning, Smart Scan entity detection (container-first two-phase pipeline), Semantic Pipeline (streaming detection, install verification, storage dashboard), AI pipeline, session persistence, performance stabilization, and Cleanup Engine with Recycle Bin all working.

**Phase 4 (System Awareness) partial** — History screen, Startup Analysis, and Quick Cleanup all implemented. Smart Recommendations and Scheduled Scanning are not yet built.
