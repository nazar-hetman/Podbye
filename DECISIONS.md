# Podbye — Decision Log

Product and architecture decisions that require written rationale. Each entry states the context, the options considered, the chosen path, and the migration plan.

---

## DEC-001 — History Screen Direction

**Date:** 2026-05-15  
**Status:** Decided — Reframe as Cleanup History

---

### What History provides today

- 30-day activity timeline strip (bar height by item count, color by risk distribution)
- Sessions table: when, target, mode, items, distribution bar, size
- Click-to-expand row: adds a detail panel with target / mode / status / duration / items / size / distribution
- Four actions per session: Open findings, Re-run with same target, Export NDJSON, Delete from history
- Timeline is hidden when fewer than 5 sessions exist (replaced with compact summary)

The underlying data is `history.json` (lightweight index) + per-session `session_{id}.json` files, all in `%LOCALAPPDATA%\Podbye\sessions\`. Cleanup operations write `cleanup_{timestamp}.json` in the same directory but are never displayed.

---

### Three user scenarios

**Scenario 1 — "I want to re-scan the same folder"**  
User scanned `C:\Projects` two weeks ago and wants to check whether anything has changed. They go to History, expand the row, click "Re-run with same target."

- **Keep as-is**: Works today. One extra click vs. putting re-run actions on Home.
- **Reframe**: Re-run stays; scan sessions are still listed, just condensed below cleanup records.
- **Absorb into Home**: Home shows last 2–3 scans with "Re-run" buttons. No extra screen needed.

*Verdict on this scenario: Re-run is useful, but the full History screen isn't required to deliver it. Home handles it fine.*

---

**Scenario 2 — "I want to reopen findings from a scan I ran last week"**  
User ran a scan but got interrupted before reviewing the results. They want to go back to that scan's findings.

- **Keep as-is**: History → expand row → Open findings. Works but is an indirect path.
- **Reframe**: Session list survives in a condensed section. Open findings still available.
- **Absorb into Home**: Home exposes the last N sessions. Open findings from there.

*Verdict on this scenario: Useful but Home already covers the most recent session. Older sessions matter only if the user regularly scans multiple targets — a niche case. The top-1 case is already handled by Home.*

---

**Scenario 3 — "I deleted some stuff last month — what did I remove?"**  
User used the cleanup flow and moved items to the Recycle Bin. Now they want to audit what was removed: what categories, how much space, any mistakes to undo.

- **Keep as-is**: Not supported. Cleanup records exist on disk but are never displayed.
- **Reframe**: Cleanup history becomes the primary view. Records show timestamp, mode, items freed, category breakdown, and per-path details. This is a genuine trust and accountability feature.
- **Absorb into Home**: Cleanup history wouldn't naturally belong on Home. It would require a separate surfacing mechanism anyway.

*Verdict on this scenario: This is the strongest argument for a dedicated History screen. It's unique value that no other screen provides, and it differentiates Podbye from opaque cleaners by giving users a clear audit trail.*

---

### Decision: Reframe as Cleanup History (Option 2)

**Chosen path:** Change History from "scan session log" to "cleanup audit log + condensed scan list."

**Rationale:**

1. **Cleanup history is unique value.** No other screen in Podbye shows what was removed and when. Surfacing `cleanup_{timestamp}.json` records turns the History screen from a log-viewer into a trust and accountability feature — a meaningful product differentiator.

2. **Scan session list is mostly redundant.** The Home screen already shows the most recent session with Open Findings, Resume, and Start New. The cases where a user wants to open session N-2 or N-3 are real but infrequent. Condensing sessions to a secondary list is a net improvement in information density.

3. **Implementation cost is low.** The cleanup records are already written to disk with a rich schema (`total_bytes_freed`, `succeeded_count`, `items` list with path/name/size/risk/category, `errors_by_path`). Displaying them requires reading the files — no new backend work.

4. **Preserves the sidebar slot.** Absorbing into Home would free a slot but also add complexity to an already multi-state Home screen. History as a dedicated cleanup audit trail earns its navigation slot.

5. **Removes the timeline without loss.** The 30-day bar chart was cosmetic and rarely meaningful for users who scan a handful of times. Replacing it with a list of cleanup events is more informative at the same height.

---

### Migration plan

The reframe does not change `session_store.py` or any session file format. It is entirely a UI change to `app/screens/history.py`.

**Step 1 — Add cleanup record loader to `session_store.py`**
```python
def load_cleanup_records() -> list[dict]:
    """Return all cleanup_{timestamp}.json records, newest first."""
```
Reads `cleanup_*.json` from the sessions directory, skips corrupted files, sorts by `timestamp` descending.

**Step 2 — Rebuild `HistoryScreen._build_content()`**

Primary section — "CLEANUP HISTORY":
- If no cleanup records: show a subtle empty state ("No cleanup actions yet. Use the Findings screen to move items to the Recycle Bin.")
- Each record: timestamp, mode badge (Recycle Bin / Permanent), bytes freed, succeeded/failed counts, category breakdown
- Expand row → shows per-item list (path, size, risk, category) and any errors

Secondary section — "PREVIOUS SCANS" (condensed):
- Collapsed list of scan sessions: when, target, mode, items count
- Two actions per row: Open findings, Re-run
- Export NDJSON and Delete keep their current behavior
- No 30-day timeline

**Step 3 — Update `HistoryScreen.refresh()`**
Call both `load_history()` and `load_cleanup_records()` and merge.

**Step 4 — Update docs**
- `ROADMAP.md` Phase 4 section
- `QT_COMPONENT_SPEC.md` if new widget patterns emerge
- `Next Steps.md` Known Limitations (remove cleanup history entry)

---

### What is NOT changing

- `history.json` index and `session_{id}.json` files — unchanged, still needed for session restore via Home screen
- `cleanup_{timestamp}.json` schema — already correct, no migrations needed
- The `open_session_requested` and `rerun_requested` signals on `HistoryScreen` — still emitted, still wired to main window
- The cleanup engine, session store write path, or any non-UI code

---
