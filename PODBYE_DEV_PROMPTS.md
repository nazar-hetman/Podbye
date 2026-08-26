# Podbye — Development Prompts

Ordered by priority. Each prompt is self-contained and can be pasted into a fresh coding session. Every prompt explicitly lists which project documents must be updated when the work is complete.

**Priority legend:**
- **P0** — Blockers for product viability. Do these first, in order.
- **P1** — Compounding tech debt. Tackle right after P0.
- **P2** — Safety and polish. Schedule between P0/P1 work.
- **P3** — Future value. Park until P0–P1 done.

---

## P0-1 — Container-First Semantic Detection Rewrite ✅ COMPLETE (2026-05-15)

### Context

Podbye's current entity detector produces incorrect groupings for monolithic applications and large distributions. Concrete reproducers found in the wild:

- `C:/Program Files/QGIS 3.40.11/apps/Qt5/qml/QtCharts/designer/images` is detected as a standalone `photo_collection`, but it is a subfolder of the QGIS installation.
- `C:/texlive/2025/texmf-dist/doc/context/documents` is detected as a `document_folder`, but it is part of the TeX Live distribution.

Root cause: the 9-pass detector runs content-homogeneity analysis (Pass 6) before it has fully established application/container roots, so content-based grouping fires on subdirectories of installations the system should have already claimed as one monolith.

### Goal

Rewrite the semantic detection pipeline as a **two-phase, container-first** algorithm. Containers always win over content. No file inside a known container should ever appear as a separate entity, regardless of its extension or sibling pattern.

### Required architecture

**Phase 1 — Discovery (shallow, fast):**
Walk only the top 2–4 levels of the scan target. Identify every "entity root" before any content classification runs. An entity root is any of:

1. First-level directory inside `Program Files`, `Program Files (x86)`, `%LOCALAPPDATA%\Programs`, `%APPDATA%\Local\Programs`.
2. Any folder containing an uninstaller (`unins000.exe`, `uninstall.exe`, `Uninstall.exe`) or matching a Windows registry Uninstall entry's `InstallLocation`.
3. Every direct child of `steamapps/common/`, `Epic Games/`, `GOG Galaxy/Games/`, `Ubisoft/games/`, `Riot Games/`, `Battle.net/`.
4. Any folder name matching the **known monolith list** (see below) anywhere on disk.
5. Any folder containing a recognized SDK marker file (`package.json` + `node_modules/`, `pyproject.toml`, `Cargo.toml`, `.git/`).

**Known monolith list** (hard-coded, extensible via settings):
```
texlive, miktex, anaconda3, miniconda3, conda, MATLAB, R-*, Cygwin, msys64,
vcpkg, .nuget, .gradle, .m2, AndroidSDK, android-sdk, JavaSoft, JetBrains,
Microsoft Visual Studio, Microsoft SDKs, Windows Kits, QGIS *, ParaView *,
Blender Foundation, Unity Hub, Epic Games, Unreal Engine, Godot, Krita,
GIMP, Inkscape, OBS Studio, FFmpeg, ImageMagick, Wireshark, Python3*,
PostgreSQL, MySQL, MongoDB, Redis, Docker, Podman
```

**Phase 2 — Assignment (streaming, deep):**
During the full filesystem walk, every file is checked against the root map. The first matching root (longest prefix wins) claims the file unconditionally. Files with no matching root go into an `untracked` pool. Content-homogeneity grouping operates **only** on the `untracked` pool.

### The Containment Rule (non-negotiable)

```
if any(file_path.startswith(root_path) for root_path in entity_roots):
    file belongs to that root, FULL STOP.
    No content classification.
    No "media collection inside QGIS".
    No exceptions.
```

### Acceptance criteria

1. Scanning `C:/` with QGIS, TeX Live, Anaconda, and Steam installed produces **one entity per installation**, not dozens of fragments.
2. Each large monolith reports correct total size (sum of all child files), not partial.
3. Operator feed clearly logs the phase split: `[smart] phase 1: discovery — found N entity roots` then `[smart] phase 2: assignment — claimed X files, Y untracked`.
4. Existing tests pass. Add regression tests for the QGIS and TeX Live cases using fixtures.
5. Performance not worse than current (target: discovery phase under 2s for a typical Windows user folder).
6. The known monolith list is overridable via `settings/scan.monolith_patterns` in config.

### Documents to update

- **SEMANTIC_PIPELINE.md** — Rewrite the Pipeline Stages section. Replace the 9-pass description with the two-phase Discovery/Assignment model. Add an explicit "Containment Rule" subsection. Add the known monolith list as a documented constant.
- **Next_Steps.md** — Move "Container-first detection" from issues list to Implemented Systems. Remove the QGIS/TeX Live limitation note.
- **ROADMAP.md** — Update Phase 2 status note to reflect the rewrite. Add a sub-bullet under Phase 2 ✅ for "Container-first semantics".
- **Readme.md** — Update Smart Scan Mode section to describe the new two-phase approach.

---

## P0-2 — Cleanup Engine with Recycle Bin ✅ COMPLETE (2026-05-15)

### Context

Podbye currently shows a cleanup preview panel with risk breakdown, file count, and reclaimable size — but the "Move to Recycle Bin" button is permanently disabled. The product cannot deliver on its core promise (helping users actually reclaim space). Phase 3 in the roadmap is the only major capability still completely missing.

### Goal

Implement a real, safe, undoable cleanup engine. Default destination is the Windows Recycle Bin (recoverable). Permanent delete must be opt-in per operation, gated behind a settings flag, and never available for Protected items under any circumstance.

### Required components

**`app/services/cleanup_engine.py`** — new module exposing:
- `CleanupWorker(QThread)` — batched deletion with progress signals
- `move_to_recycle_bin(paths: list[Path]) -> CleanupResult` using `SHFileOperationW` with `FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT` flags (or `winshell.recycle_bin().delete()` as a fallback, but native API preferred for batching).
- `permanent_delete(paths: list[Path]) -> CleanupResult` — only callable when settings explicitly enable it AND no Protected items are in the list. Implementation must double-check protection at delete time, not at queue time.
- `CleanupResult` dataclass: `succeeded`, `failed`, `skipped_protected`, `total_bytes_freed`, `errors_by_path`.

**Confirmation flow:**
- Clicking the cleanup button opens a modal (not the current inline preview — escalate to modal for irreversible-ish action).
- Modal shows: total items, total size, breakdown by risk level, **per-item path list for Risk/Review items** (scrollable, expand-on-demand for >20 items).
- If any Risk-level items are selected, the modal requires typing a confirmation phrase (e.g., "delete X items") before the action button enables.
- Protected items in the selection are visibly excluded from the action with a "X protected items will be skipped" note.

**Cleanup history:**
- Each cleanup operation creates a `cleanup_{timestamp}.json` in the session folder.
- Record: timestamp, session_id, items (path, size, risk, category, destination), total_bytes_freed, mode (recycle_bin / permanent).
- Cleanup history visible in History screen as a separate row type (distinguish from scan rows).

**Empty-state messaging post-cleanup:**
- After successful cleanup, the originating category block updates immediately (new size, item count, recompute reclaimable).
- Toast / status bar message: "12 items moved to Recycle Bin · 24.5 GB freed".
- Findings stays on the same screen with the cleaned items removed (no jarring navigation).

### Acceptance criteria

1. User can select items in Findings, hit Cleanup, confirm in modal, and see them move to the Recycle Bin.
2. Files actually appear in the Recycle Bin and can be restored manually by the user.
3. Protected items can never be deleted. Force-attempting via direct service call must raise `ProtectedPathError`.
4. Permanent delete is reachable only by enabling the setting AND typing the confirmation phrase. Default is disabled.
5. Errors (locked files, permission denied) are caught per-path; one failure does not abort the batch.
6. Cleanup is logged to Operator Feed in real time: `[cleanup] moving 23 items... [cleanup] done: 22 succeeded, 1 failed (file in use)`.
7. After cleanup, the Findings dashboard reflects the new state without requiring a re-scan.
8. Atomic-feeling for the user: either the modal completes or it doesn't. Mid-batch UI does not let the user start another cleanup.

### Documents to update

- **ROADMAP.md** — Move Phase 3 "Cleanup Engine" from ⏳ to ✅. Update the partial-status note. Update "Current Status" footer.
- **Next_Steps.md** — Remove "Cleanup engine" from technical debt. Remove from "Known Limitations". Update "Immediate Next Steps" priority list (it was item #1, now done).
- **Readme.md** — Change status block from "🟡 safe cleanup recommendations (infrastructure built, no deletion yet)" to "✅ safe cleanup with Recycle Bin recovery". Update Phase 3 description.
- **SEMANTIC_PIPELINE.md** — Add a Cleanup section describing how SemanticEntity selection translates to per-file delete operations.

---

## P1-1 — Findings Consolidation ✅ COMPLETE (2026-05-15)

### Context

The Findings experience currently lives across two files: `app/screens/findings.py` (legacy flat table) and `app/screens/findings_dashboard.py` (new treemap). Both are reachable through navigation. This creates a maintenance hazard — any change to filtering, selection, or AI integration risks divergence between the two.

### Goal

Make `findings_dashboard.py` the canonical Findings screen. Repurpose `findings.py` strictly as a **detail subview** used inside `CategoryDetailView` for entity-row expansion. Remove the duplicate sidebar entry.

### Required changes

1. Remove the legacy Findings item from the sidebar navigation. Only the dashboard-based Findings exists in nav.
2. Inside `CategoryDetailView`, when a user clicks a row's expand arrow, render the legacy `FindingDetail` widget as the expanded subview (already happens — verify the import path and that it's not loading a parallel data source).
3. Audit `findings.py` for any code that is unreachable after this change. Delete dead screens; keep only the FindingDetail widget and supporting helpers it actually needs.
4. Ensure that any cleanup flow, export, or selection state lives in the dashboard layer and not in the legacy screen.
5. If the legacy table view is genuinely useful for power users (advanced filter, bulk operations not yet replicated in the dashboard), keep it accessible via a **"Show as Table" button in the dashboard topbar**, not as a separate nav item.

### Acceptance criteria

1. Sidebar has exactly one Findings entry.
2. No screen routing leads to the legacy flat findings table by default.
3. `FindingDetail` widget still works for expanding rows in CategoryDetailView (manual verification).
4. Codebase has no orphaned screen classes (no `FindingsScreen` class registered in main.py that doesn't get reached).
5. If "Show as Table" is implemented, it lives in the dashboard, shares the same data, same selection state, same filters.

### Documents to update

- **Readme.md** — Update Project Structure section. Remove `findings.py` from the "screens/" listing OR mark it explicitly as `legacy detail view (not a screen)`.
- **SEMANTIC_PIPELINE.md** — In the UI Integration section, remove the "Legacy Findings Screen" subsection or mark it explicitly as deprecated/internal.
- **ROADMAP.md** — Add note under Phase 2 ✅ confirming Findings is unified.
- **QT_COMPONENT_SPEC.md** — Remove any references to two parallel Findings screens.

---

## P1-2 — QAbstractTableModel Migration ✅ COMPLETE (2026-05-15)

### Context

Findings tables use `QTableWidget` with full widget allocation per cell. For scans producing >100k findings (which becomes normal once container-first detection is correct and users start scanning full drives), this consumes ~500MB of widget memory and stutters on scroll, filter, and sort. The existing performance mitigations (throttled refresh, dict cache, `setUpdatesEnabled` wrapping) are workarounds, not fixes.

### Goal

Migrate the findings table to a proper Model/View architecture: `QAbstractTableModel` for data, `QTableView` for display, `QSortFilterProxyModel` for filtering and sorting. This enables virtual scrolling (only visible rows render), drops memory usage substantially, and makes filtering effectively free.

### Required changes

1. Create `app/models/findings_table_model.py` exposing `FindingsTableModel(QAbstractTableModel)` backed by the same list of `SemanticEntity` / `Finding` objects already in `ScanState`.
2. Implement `data()`, `rowCount()`, `columnCount()`, `headerData()`, `flags()`. Use `Qt.UserRole` to expose the underlying entity object for selection logic.
3. Replace `QTableWidget` usage in `CategoryDetailView` (and any "Show as Table" view) with `QTableView` plus the new model and a `QSortFilterProxyModel`.
4. Move filter chip logic (Safe/Review/Risk/Protected, search text) to the proxy model's `filterAcceptsRow()`. Toggling chips becomes a model invalidation, not a table rebuild.
5. Preserve all existing visual styling (badge cells, risk dots, mono fonts) via a custom `QStyledItemDelegate`.
6. Keep selection state coherent across filter changes — entities checked while filtered should remain checked when filter is cleared.

### Acceptance criteria

1. Scanning 500k items: scroll is smooth, filter changes apply in <50ms, sort applies in <100ms.
2. Memory usage at 500k items drops by at least 60% versus the QTableWidget baseline.
3. All existing tests pass. Add a stress test with synthetic 500k-row fixture.
4. Selection works correctly across filter changes (test: select 5 items, filter to only safe, unfilter, verify all 5 still selected).
5. Operator feed no longer logs `[perf] table populate ... ms` warnings for table operations under 100k rows.

### Documents to update

- **Next_Steps.md** — Remove "QTableWidget → QAbstractTableModel" from technical debt. Update Performance Notes.
- **ROADMAP.md** — Add under Phase 5 ✅ or as a Phase 2.x improvement: "QAbstractTableModel migration".
- **QT_COMPONENT_SPEC.md** — Replace the QTableWidget styling section with QTableView + QStyledItemDelegate. Document the delegate.
- **Readme.md** — No change required unless Project Structure mentions specific table widgets.

---

## P2-1 — Cloud-Sync Safety Detection ✅ COMPLETE (2026-05-15)

### Context

Users can have OneDrive, Dropbox, Google Drive, or iCloud folders inside their normal scan targets. These often appear as local files (cloud "placeholder" files via reparse points) but deletion propagates to the cloud and to all other synced devices. Podbye currently has no awareness of this — a user cleaning up "old photos" could accidentally delete files visible to their team.

### Goal

Detect cloud-synced paths and treat them with elevated caution: force `Review` risk minimum, display a visible "cloud sync" badge, and require explicit acknowledgment before any cleanup touches them.

### Required detection

**Path-based detection (fast, first pass):**
- `%USERPROFILE%\OneDrive*`, `%USERPROFILE%\OneDrive - *` (work accounts)
- `%USERPROFILE%\Dropbox`, `%USERPROFILE%\Dropbox (*)`
- `%USERPROFILE%\Google Drive`, `%USERPROFILE%\GoogleDrive`, paths containing `My Drive`
- `%USERPROFILE%\iCloudDrive`
- `%USERPROFILE%\Box`, `%USERPROFILE%\pCloud Drive`
- Detect any of the above being a junction point to a different volume.

**Reparse-point detection (authoritative, second pass):**
- For any path inside a candidate cloud folder, check `GetFileAttributesW`. If `FILE_ATTRIBUTE_REPARSE_POINT` is set, read the reparse tag.
- Tags of interest: `IO_REPARSE_TAG_CLOUD`, `IO_REPARSE_TAG_CLOUD_1` through `IO_REPARSE_TAG_CLOUD_F`, `IO_REPARSE_TAG_ONEDRIVE`.
- Any match means the file is a cloud placeholder, not local data.

### Required behavior

1. Every entity whose root is a cloud-synced folder gets a new field `cloud_sync_provider: str | None`.
2. Entities with cloud_sync_provider set get risk forced to at least `Review`. Existing risk does not downgrade them; cloud only raises minimum risk.
3. Findings dashboard shows a small cloud icon (☁) in the badge area for cloud-synced categories.
4. Cleanup modal explicitly lists cloud-synced items in a separate group: "**Cloud-synced items (will sync deletion to your cloud account):**". This group requires a separate checkbox acknowledgment before cleanup proceeds.
5. Operator feed: `[cloud] detected OneDrive sync root at C:\Users\...\OneDrive` and `[cloud] 14,231 items in cloud-synced paths`.

### Acceptance criteria

1. Scanning a user folder with OneDrive present: OneDrive shows as its own entity with cloud badge.
2. Files inside OneDrive cannot be cleaned without separate acknowledgment.
3. Reparse-tag detection works correctly for OneDrive Files-On-Demand placeholders (these have 0 bytes on disk; ensure size reporting doesn't lie to the user).
4. If detection fails (permission errors, unrecognized tags), default to treating the path as cloud-synced — fail safe, not unsafe.

### Documents to update

- **SEMANTIC_PIPELINE.md** — Add new section "Cloud-Sync Safety" describing detection and behavior. Add `cloud_sync_provider` to the SemanticEntity data model documentation.
- **ROADMAP.md** — Move "Cloud Sync Awareness" from Phase 5 Future to a completed sub-item under Phase 3 (Cleanup Safety Foundation).
- **Readme.md** — Add cloud-sync awareness to the feature list.
- **DESIGN_RULES.md** — Document the cloud badge variant (icon, color usage).

---

## P2-2 — UI Polish Batch ✅ DONE

### Context

Three observed glitches that are individually small but accumulate to make the product feel rougher than it actually is:

1. **Startups table row rendering** — visible in current screenshots: text rows appear to overlap with horizontal row borders. Likely insufficient vertical padding or a baseline/line-height conflict in the QSS for the startups table.
2. **History timeline empty state** — when fewer than 5 sessions exist, the 30-day timeline strip is mostly empty bars and looks broken rather than informative.
3. **Theme switch does not refresh in-place** — screens with inline `setStyleSheet` calls (Home, Analyze, Quick Cleanup) don't update until you navigate away and back. Already documented in Next_Steps.md but unresolved.

### Goal

Fix all three in one polish pass. Each is small individually; batching them is more efficient than separate cycles.

### Required changes

**Startups table:**
- Audit the QSS for `QTableWidget::item` and `QHeaderView::section` in the Startups screen specifically.
- Confirm vertical padding is at least 10px top + 10px bottom (current spec is 8px which is likely the cause when combined with font baseline).
- Add `line-height: 1.4` equivalent via `QFontMetrics` or explicit row height calculation.
- Confirm `border-bottom: 1px solid {border}` is not drawn over text by checking the row delegate paint order.

**History empty state:**
- If `len(sessions) < 5`, hide the 30-day timeline strip entirely and replace with a single-line summary: `1 session · 37 MB scanned · most recent today 12:27`.
- Below the summary, show the session list directly (as already implemented).
- Once `len(sessions) >= 5`, the timeline returns.

**Theme switch refresh:**
- Define a `theme_changed` signal on `ThemeManager`.
- Every screen subscribes in its constructor: `theme_manager.theme_changed.connect(self._rebuild_styles)`.
- Each screen implements `_rebuild_styles()` that re-applies inline stylesheets using the current palette.
- Avoid full screen reconstruction — only re-apply styles.

### Acceptance criteria

1. Startups table rows have visible vertical breathing room. Text never touches the row border. Screenshot before/after attached to PR.
2. History screen with 1 session shows a clean summary, not an empty bar chart.
3. Switching themes in Settings updates every visible screen immediately, no navigation required. Test all 4 themes.

### Documents to update

- **Next_Steps.md** — Remove "Theme switch does not rebuild screens" from Known Limitations.
- **DESIGN_RULES.md** — Document the minimum table row padding (10/10/10/10 px).
- **QT_COMPONENT_SPEC.md** — Document the `theme_changed` signal and screen subscription pattern.

---

## P2-3 — History Re-Evaluation (Discovery, not Implementation) ✅ DONE

### Context

History exists but its product value is uncertain. Most users will scan their primary drive a small number of times. The 30-day timeline rarely has dense data. The "Re-run with same target" action is useful but could live elsewhere. Before investing further in History (or before cutting it), worth a deliberate audit.

### Goal

Decide the future of the History screen with a written rationale. Pick one of three paths:

1. **Keep as-is** — current implementation stays, no further investment. Document why.
2. **Reframe** — change History from "session log" to something more useful (e.g., "cleanup history" focused on what was deleted and when, with restore-from-Recycle-Bin hints; or "scan diff" showing what changed since the last scan of the same target).
3. **Absorb into Home** — eliminate History as a separate screen, surface last-3-scans and "re-run" actions on Home directly. Free a slot in the sidebar.

### Required deliverable

A short decision document (`DECISIONS.md` at project root) with:
- Recap of what History provides today.
- Three user scenarios where History is actually used (or might be) and how each option serves them.
- The chosen option with reasoning.
- Migration steps for whichever option wins.

### Acceptance criteria

This is a planning task, not a code task. Done = a written decision in `DECISIONS.md` and the corresponding update to ROADMAP.md reflecting the chosen direction.

### Documents to update

- **DECISIONS.md** (new file) — Houses this decision and future ones like it.
- **ROADMAP.md** — Update Phase 4 section to reflect chosen direction for History.
- If "Absorb into Home" wins: **Readme.md** Project Structure section, **QT_COMPONENT_SPEC.md** Sidebar section.

---

## P3 — Duplicate Detection + Age-Based Heuristics ✅ DONE

### Context

Park this until P0–P1 done. Mentioned here so it is recorded, not lost.

### Goal

Add two intelligence layers that meaningfully improve "what is safe to clean" recommendations:

1. **Duplicate detection** — find files with identical content across the scan target. Show consolidated reclaimable space.
2. **Age-based heuristics** — files not modified in years are stronger cleanup candidates, especially in dev artifacts, ISOs, installers, downloads.

### Required components

**Duplicate detection:**
- Hash files larger than a configurable threshold (default 10 MB; smaller files have low ROI for dedup).
- Use a fast, content-addressed hash (BLAKE3 if available, else SHA-256).
- Group hashes during the scan; expose duplicates as a new `DuplicateGroup` entity type.
- Findings shows: "X GB in N duplicate groups across M files".
- Cleanup workflow for duplicates: user picks which copy to keep; others go to Recycle Bin.

**Age heuristics:**
- For every entity, compute `last_modified_recursive` (max mtime across all files).
- For files specifically, expose `mtime` in the CategoryDetailView as a new "Age" column.
- Risk and reclaimable-score formulas factor in age: not-modified-in-2y boosts safe-cleanup score by 20%, not-modified-in-5y by 40%.
- This applies only to specific entity types (dev_artifacts, installer_group, archive_group, downloads); user data (photos, documents) is never down-prioritized for being old.

### Acceptance criteria

1. Findings dashboard shows a dedicated "Duplicates" category when duplicates exist.
2. CategoryDetailView shows Age column for entities where it applies, with mono-formatted relative dates ("2y 3m").
3. Reclaimable space estimates account for both duplicates and age boosts. Numbers are documented as estimates, not guarantees.
4. Performance: hashing only runs on files >threshold, in a separate background worker, never blocks the scan.

### Documents to update

- **SEMANTIC_PIPELINE.md** — Add `DuplicateGroup` entity type and the age-heuristic scoring formula.
- **ROADMAP.md** — Move "Duplicate Detection" and "Age-based Risk" from Phase 5 to whatever phase you tackle them in. Mark complete.
- **Readme.md** — Add duplicate detection and age awareness to feature list.

---

## Notes on using these prompts

- Each prompt is independent except for the order. P0-1 should land before P0-2 because cleanup is more valuable when entities are grouped correctly. P1-1 should land before P1-2 because there is no point migrating two table screens to QAbstractTableModel.
- Every prompt explicitly names the documents to update. Treat documentation updates as part of "done", not as cleanup.
- If a prompt grows unexpectedly during implementation, stop and split it. None of these should turn into multi-week branches; if one does, the scope was wrong.
