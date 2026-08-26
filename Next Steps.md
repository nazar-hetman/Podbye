# Podbye — Next Steps & Current State

Last updated: 2026-05-16

---

## Implemented Systems (Current)

### ✅ Core Scanning
- Recursive `os.walk` via `ScanWorker` QThread
- 200-item batches, 500-item progress reporting
- Symlink/junction skipping, permission error handling
- Resume dedup via `skip_paths` (normalized path set)
- Scan rate tracking (items/s in final log)

### ✅ Smart Scan Entity Detection
- Two-phase container-first detection pipeline (Phase 1 Discovery + Phase 2 Assignment)
- Phase 1 Discovery: shallow walk (≤ 4 levels), claims known monolith roots by name pattern before any content analysis — enforces the Containment Rule
- Known monolith list: TeX Live, MiKTeX, MATLAB, QGIS, ParaView, Cygwin, MSYS2, vcpkg, Android SDK, JetBrains, Blender Foundation, Unity Hub, Python versioned installs, R statistical computing, Microsoft Visual Studio / SDKs / Windows Kits, FFmpeg, ImageMagick, Wireshark, PostgreSQL, MySQL, MongoDB, Redis, Docker, Podman — overridable via `settings/scan.monolith_patterns`
- Phase 2 Assignment: 9-pass semantic classification runs on untracked pool only
- Indexed lookups (children_index dict) — O(k) not O(n)
- 26+ entity types detected (applications, games, dev projects, caches, media collections, databases, AI models, browser profiles, etc.)
- ~60 app markers (Steam, Ollama, Unity, Unreal, Godot, JetBrains family, VS Code, Epic/GOG/Riot/Battle.net, Brave, Slack, Telegram, Spotify, etc.)
- Content homogeneity analysis (photo/video/audio/document/archive/installer/AI model/backup/log/photogrammetry) — runs only on untracked pool
- Database detection (.sqlite/.db > 1MB)
- Protected path detection (Windows, System32, ProgramData, critical AppData)
- Performance timing logged

### ✅ Risk & Classification
- 4-level risk: Safe, Review, Risk, Protected
- Rule-based categorization (11 categories)
- Risk reasons tracked per finding
- Protected paths never classified as Safe
- Recommendations vary by risk level

### ✅ AI Explanation Pipeline
- Ollama REST API integration (local only by default)
- Prioritized queue: Risk → Review → Unknown → Safe
- Protected items auto-disabled (never explained)
- Concurrent workers (default 3, configurable 1-8)
- Emergency timeout (180s default)
- Disk cache: `%LOCALAPPDATA%\Podbye\cache\ai\`
- Cache key: path + size + mtime + model + tone + language + length
- Entity-specific prompts (grouped context, file samples)
- Language support: English, Ukrainian
- Tone: neutral, concise, detailed
- Length: compact, standard, detailed
- `queue_progress` signal for live telemetry (done, total, active, failed)
- `queue_finished` signal with RuntimeError safety
- Stale-result protection via session_id

### ✅ Session Persistence
- Auto-save every 8s during scan (`_AUTOSAVE_MS = 8000`)
- Save on app close (closeEvent)
- Save on scan complete / AI complete
- `last_run.json` in `%LOCALAPPDATA%\Podbye\sessions\`
- Restore: findings, entities, target, mode, session_id
- Resume: skip known paths, continue scanning new items
- Home screen shows resume panel for unfinished sessions

### ✅ Home Screen (Session-Aware)
- Empty state (no session)
- Last-run state (completed session summary)
- Live state (scan in progress awareness)
- Resume state (unfinished session with Resume/Start new buttons)
- Session detail display (target, count, size, risk breakdown)

### ✅ Performance Stabilization
- ScanWorker: 200-item batches, 500-item progress intervals
- ScanState: 400ms throttled UI refresh signal
- Entity detection: background thread, indexed lookups
- OperatorFeed: QPlainTextEdit (not per-line QLabels), max line cap, batch append
- Findings: `setUpdatesEnabled` wrapping, throttled refresh via QTimer
- AI updates: batched finding updates, avoid detail widget thrashing
- Dict caches: findings_as_dicts/entities_as_dicts rebuilt only when dirty
- Perf diagnostics: slow ops (>50-100ms) logged to operator feed

### ✅ AI Retry (2026-05-16)
- "Retry failed" button in Analyze screen operator feed header — visible when failures exist and queue is idle
- "Retry failed" button in Startups screen header — same visibility conditions
- Resets `ai_status`/`ai_explanation`/`ai_error` on each failed item before re-queueing
- Does not re-run successful or pending explanations

### ✅ Permanent Delete Setting (2026-05-16)
- Settings → Scan → File Handling: `perm_delete_enabled` toggle, default OFF
- Orange warning label: "Permanent delete bypasses Recycle Bin and cannot be undone."
- Persisted in `settings_store.py`; read by `CleanupWorker` at delete time

### ✅ Cloud-Sync Safety Detection (P2-1)
- `app/services/cloud_detector.py` — path-based provider detection + Windows reparse-point check
- `SmartEntity.cloud_sync_provider` field; cloud entities forced to minimum Review risk
- `☁` badge overlay in findings table for cloud-synced entities
- Cleanup modal: separate cloud warning + mandatory acknowledgment checkbox

### ✅ QAbstractTableModel Migration (P1-2)
- `FindingsTableModel(QAbstractTableModel)` in `app/models/findings_table_model.py`
- `FindingsFilterProxy(QSortFilterProxyModel)` — search text, risk filter, 5-key custom sort
- `FindingsDelegate(QStyledItemDelegate)` — risk badge painting + checkbox toggle via `editorEvent`
- `CategoryDetailView` now uses `QTableView` — virtual scrolling, no widget-per-cell overhead
- Checkbox state stored at source-model level (`_checked: set`) — filter never loses selections

### ✅ Findings Consolidation
- `FindingsDashboard` (`findings_dashboard.py`) is the single canonical Findings screen in navigation
- `FindingsScreen` (legacy flat QTableWidget screen) deleted — was never reachable from main navigation
- `FindingDetail` widget kept in `findings.py` as a standalone detail subview
- `CategoryDetailView` (inside `FindingsDashboard`) now owns selection state, cleanup, and entity drill-down
- "Move to Recycle Bin" button lives in `CategoryDetailView` selection bar — opens `CleanupConfirmDialog`

### ✅ Cleanup Engine
- `CleanupWorker(QThread)` + `CleanupResult` + `move_to_recycle_bin()` + `permanent_delete()` in `app/services/cleanup_engine.py`
- SHFileOperationW with FOF_ALLOWUNDO — sends to Windows Recycle Bin (recoverable)
- `ProtectedPathError` enforced at delete time (not queue time)
- Per-path error isolation — one failure never aborts the batch
- `CleanupConfirmDialog(QDialog)` in `app/screens/cleanup_dialog.py`:
  - Risk breakdown, per-item path list for Risk/Review, protected exclusion note
  - Confirmation phrase required for Risk-level items ("delete N items")
  - Live progress + result summary with close button
- `ScanState.remove_entities_by_path(paths)` removes cleaned entities and emits `ui_refresh`
- Toast message in selection bar post-cleanup: "✓ N items moved · X freed"
- Cleanup record written to `%APPDATA%/Podbye/sessions/cleanup_{timestamp}.json`
- Protected entity system: Protected risk level, badge, filter chip, never sent to AI, never deleted

### ✅ Stop/Resume
- Single Start/Stop toggle button
- Graceful halt (ScanWorker.halt())
- Full stop (scan + AI queue cancel)
- Partial results preserved
- Scanner-level dedup on resume

### ✅ History Screen (DEC-001 — 2026-05-16)
- **Primary section:** Cleanup audit log from `cleanup_{timestamp}.json` — table with WHEN / MODE badge / FREED / ITEMS; click-to-expand `CleanupRecordDetail` (per-item list, ERR badges, Recycle Bin restore note)
- **Secondary section:** Condensed scan sessions from `history.json` + `session_{id}.json` — WHEN / TARGET / MODE / ITEMS; click-to-expand with Open/Re-run/Delete actions
- `load_cleanup_records()` in `session_store.py` — globs cleanup files, newest first
- Empty states per section; global empty state when history is completely empty

### ✅ Startup Analysis
- `StartupDetector` reads Windows registry Run keys (HKCU + HKLM + WOW64 variant)
- `StartupApproved` keys determine accurate enabled/disabled state
- User and All Users startup folders (`.lnk` shortcuts — win32com if available, binary MS-SHLLINK fallback otherwise)
- Publisher lookup via `VS_VERSIONINFO` exe resource (reads CompanyName)
- Risk classification: Protected (Microsoft system + ctfmon/windefend) → Risk (AV/VPN/drivers) → Review (game launchers/cloud sync/update helpers) → Safe (optional apps)
- Deduplication by `source|name` key, sorted Risk → Review → Protected → Safe, enabled first
- `StartupEntry` model: name, command, path, publisher, source, source_label, enabled, risk, risk_reason, impact, AI fields
- Unresolved .lnk targets noted in risk_reason; .lnk path shown as fallback command
- Podbye does **not** modify startup entries — recommendation only

### ✅ Quick Cleanup (2026-05-16)
- `QuickCleanupDetector(QThread)` in `app/services/quick_cleanup_detector.py` — five scanners in background thread
- Detects: User Temp (`%TEMP%` / `%LOCALAPPDATA%\Temp` contents), Browser Cache (Chrome / Edge / Brave / Firefox / Opera / Vivaldi cache dirs), Thumbnail Cache (`thumbcache_*.db` files), Windows Update Cache (`SoftwareDistribution\Download` contents), Windows Temp (`C:\Windows\Temp` contents)
- Auto-scans on first screen show (`showEvent`); Rescan button for manual refresh
- Categories appear live as each scanner completes; empty-state shown if nothing found
- Checkbox toggles update right-panel summary (total size, item count, est. duration) in real time
- One-click cleanup: `CleanupWorker` with `MODE_RECYCLE`; progress shown inline in right panel
- Post-cleanup: item counts updated per row, cleanup record written to History
- No modal dialogs — all progress and results shown inline

### ✅ Multi-Session History Persistence
- `session_store.py` maintains `history.json` (lightweight index) + `session_{id}.json` (full data per session)
- `load_history()` → returns index list for History screen
- `load_session_by_id(id)` → loads full session data for restore/export
- `delete_session_from_history(id)` → removes index entry + deletes session file

---

## Known Limitations

### Rendering & UI
- **Detail panel is recreated each expand** — no widget recycling for FindingDetail.
- **Filter application is synchronous** — large filter changes on huge datasets may cause brief UI stutter.

### Smart Scan
- **Entity confidence is heuristic-based** — no machine learning or content inspection. Confidence is set to fixed values (0.85-0.95) based on pass quality, not actual analysis.
- **No recursive depth limit** — very deep directory trees in entity detection could theoretically be slow (mitigated by indexed lookups).
- **Entity grouping is static** — once detected, entities are not updated if the scan resumes and adds new findings.
- **Monolith list is hard-coded** — new distributions must be added to `_KNOWN_MONOLITH_PATTERNS` in entity_detector.py or supplied via `settings/scan.monolith_patterns`.

### AI Pipeline
- **Quality depends on local model** — small models (phi3, tinyllama) produce lower-quality explanations. Best results with 7B+ models.
- **No streaming** — explanations arrive as complete blocks, no incremental display.
- **Language quality varies** — Ukrainian generation quality depends on model training data; smaller models may produce mixed-language output.

### Session & State
- **No incremental session save** — entire findings list serialized each auto-save (fine for <50k items, but could be slow for very large scans).

### Cleanup
- **Permanent delete** — toggle exposed in Settings → Scan → File Handling; off by default with warning.

### Screens Still Using Mock Data
- (none remaining)

---

## Technical Debt

| Area | Issue | Priority |
|------|-------|----------|
| Findings table | QAbstractTableModel migration complete — virtual scrolling active | ✅ Done |
| Entity restore | Implemented (2026-05-16): all fields restored; resume skips re-detection when no new files found | ✅ Done |
| Quick Cleanup | ✅ Done (2026-05-16) — real detection + Recycle Bin cleanup | ✅ Done |
| Cleanup history UI | DEC-001 implemented (2026-05-16) — cleanup audit log primary, scans secondary | ✅ Done |
| Filter perf | Synchronous filter on large datasets | Low |
| Detail recycling | FindingDetail widgets not reused | Low |
| Permanent delete setting | ✅ Done (2026-05-16) — wired to Settings → Scan → File Handling | ✅ Done |
| AI retry | ✅ Done (2026-05-16) — "Retry failed" button in Analyze feed header + Startups header | ✅ Done |
| Startup LNK | ✅ Done (2026-05-16) — `_resolve_lnk_binary` MS-SHLLINK parser as fallback when win32com unavailable | ✅ Done |

---

## Immediate Next Steps (Priority Order)

1. **AI streaming** — display explanations as they generate for better UX

---

## Performance Notes

### Current Bottlenecks
1. **Findings table rebuild** — full widget recreation on filter change for large sets
2. **Dict cache rebuild** — `findings_as_dicts()` serializes all findings when cache is dirty
3. **Session auto-save** — serializes entire state every 8s (acceptable for <50k items)

### Mitigations In Place
- 400ms throttled UI refresh (prevents per-batch rebuilds)
- Findings dict cache (only rebuilt when data changes)
- `setUpdatesEnabled(False)` during table population
- Batched AI finding updates (coalesced before refresh)
- OperatorFeed max line cap (prevents unbounded growth)
- Entity detection on background thread
- Performance diagnostics logging (>50ms operations)

### Future Optimizations
- Incremental session save (delta-based)
- Entity detection incremental update on resume
- Worker pool for filter application on large sets

---

## Architecture Decisions

| Decision | Rationale |
|----------|-----------|
| QThread for scanning | Keeps UI responsive during heavy I/O |
| Background thread for entity detection | Avoids UI freeze on grouping 50k+ items |
| Throttled UI signal (400ms) | Prevents per-batch repaint storms |
| Dict cache for findings | Avoids O(n) serialization on every UI refresh |
| Indexed entity detector | O(k) child gathering instead of O(n) per entity |
| Session auto-save | Crash recovery without user action |
| Protected risk level | Safety-first: system paths can never be accidentally cleaned |
| AI queue prioritization | Users see explanations for risky items first |
| Emergency timeout (180s) | Prevents hung AI workers from blocking queue |
| Disk-cached AI results | Avoids re-explaining unchanged files across sessions |
