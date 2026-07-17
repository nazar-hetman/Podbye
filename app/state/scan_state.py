"""Shared scan state — single source of truth for scan results.

Both Analyze and Findings screens connect to this object's signals
to receive live updates during a scan.

Key design: batches are collected here and a throttled signal is emitted
at a controlled interval so the UI is never rebuilt per-batch.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid

from PySide6.QtCore import QObject, Signal, QTimer, Qt

from app.models.finding import Finding, _format_size
from app.models.smart_entity import SmartEntity

# Re-export for convenience in this module
_human_size = _format_size

# Internal lifecycle/thread breadcrumbs go here (DEBUG level) instead of the
# UI log panel — set VIGIL_DEBUG=1 (see main.py) to surface them on the console.
_log = logging.getLogger("vigil.scanstate")

# How often the UI refresh signal fires during a scan (ms)
_THROTTLE_MS = 800

# How often session state is auto-saved to disk (ms)
# Increased to reduce serialization overhead on large scans
_AUTOSAVE_MS = 30_000

# For large scans, how often (seconds) to write a FULL findings checkpoint so a
# crash stays resumable. Between these, only the small frontier + summary are
# saved (every _AUTOSAVE_MS). Bounded to avoid churning multi-hundred-MB JSON
# to disk — which would also contend with a scan of that same drive.
_FULL_CHECKPOINT_S = 120.0

# Threshold above which scans are considered "large" (affects autosave behavior)
_LARGE_SCAN_THRESHOLD = 50_000


class ScanState(QObject):
    """Global scan state shared across screens."""

    # Throttled signal — UI should connect to this, not findings_changed
    ui_refresh = Signal()

    # Emitted when scan starts (target path)
    scan_started = Signal(str)

    # Emitted when scan completes normally
    scan_finished = Signal()

    # Emitted when scan is halted early — partial results preserved
    scan_halted = Signal()

    # Emitted for each progress tick: (scanned_count, current_path)
    progress = Signal(int, str)

    # Emitted for operator feed log lines
    log_line = Signal(str)

    # Emitted when a Finding's AI fields change (forwarded from AIExplainer)
    ai_finding_updated = Signal(object)  # Finding

    # Emitted after entity detection completes in smart mode
    entities_ready = Signal()
    
    # Emitted when scan phase changes: (phase_name, message)
    # phases: "filesystem", "entity_detection", "ai_classification", "complete"
    scan_phase_changed = Signal(str, str)
    
    # Emitted during entity detection with semantic coverage stats:
    # (phase, grouped_files, ungrouped_files, entities_created, coverage_pct)
    entity_progress = Signal(str, int, int, int, int)
    
    # Internal signal for thread-safe entity results delivery from worker to main thread
    # Carries the entity list directly — emitted by worker, received on main thread
    _entity_results_internal = Signal(list, list)  # (entities, discovered_entities)

    # Emitted when new skipped/protected entries arrive from ScanWorker
    skipped_entries_updated = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._findings: list[Finding] = []
        self._dict_cache: list[dict] = []
        self._dict_cache_dirty: bool = True
        self._target: str = ""
        self._is_running: bool = False
        self._halted: bool = False
        self._stopped: bool = False
        self._scan_mode: str = "smart"  # "smart" or "all"
        self._ai_explainer = None      # set via set_ai_explainer()
        self._settings_store = None    # set via set_settings_store()

        # Session tracking
        self._session_id: str = ""
        self._start_time: float = 0.0
        self._known_paths: set = set()  # for resume dedup

        # Smart mode entity storage
        self._entities: list[SmartEntity] = []
        self._entity_dict_cache: list[dict] = []
        self._entity_dict_dirty: bool = True

        # Skipped / protected entries from scanner
        self._skipped_entries: list[dict] = []  # {path, name, reason, description}
        
        # Scan phase tracking
        self._current_phase: str = "idle"  # idle, filesystem, entity_detection, ai_classification, complete
        self._entity_detection_running: bool = False
        self._entity_detection_cancelled: bool = False
        
        # AI run mode tracking (new, resume, restore)
        self._run_mode: str = "new"

        # Resume: findings count captured after restore_from_session, so that
        # set_running(False) can detect "no new files found" and skip re-detection.
        self._resume_baseline_count: int = 0

        # Aggregation caches (updated incrementally)
        self._cat_counts: dict = {}    # {cat: int}
        self._cat_sizes: dict = {}     # {cat: int}
        self._risk_counts: dict = {}   # {risk: int}
        self._total_size: int = 0

        # Resume frontier: directories the scanner hasn't walked yet.
        # _scan_frontier is updated live by the running worker (for checkpoints);
        # _resume_frontier is the frontier restored from a saved session, handed
        # to the next ScanWorker so it continues instead of re-walking.
        self._scan_frontier: list = []
        self._resume_frontier: list = []
        self._last_full_checkpoint: float = 0.0  # time of last full-findings save

        # Roots being scanned. One for a folder/drive scan; several for "Scan
        # all drives". Entity detection partitions findings by these so each
        # drive is grouped against its own root.
        self._scan_roots: list = []

        # Lazy {normalized_path: SmartEntity|Finding} index, built on the first
        # find_by_path() call and dropped whenever findings/entities change.
        self._path_index: dict | None = None

        # In-flight background session saves. Daemon threads are killed
        # abruptly at interpreter exit, so shutdown joins these to make sure a
        # save that was already started actually lands on disk.
        self._save_lock = threading.Lock()
        self._save_threads: set = set()

        # Throttle timer
        self._throttle = QTimer(self)
        self._throttle.setInterval(_THROTTLE_MS)
        self._throttle.setSingleShot(False)
        self._throttle.timeout.connect(self._flush_ui)
        self._pending_ui = False

        # Auto-save timer
        self._autosave = QTimer(self)
        self._autosave.setInterval(_AUTOSAVE_MS)
        self._autosave.setSingleShot(False)
        self._autosave.timeout.connect(self._autosave_session)
        
        # Connect internal signal for thread-safe entity results delivery
        # QueuedConnection ensures handler runs on main thread even when emitted from worker
        self._entity_results_internal.connect(
            self._on_entity_results_delivered, 
            type=Qt.ConnectionType.QueuedConnection
        )

    # ── Properties ───────────────────────────────────────────

    @property
    def findings(self) -> list[Finding]:
        return self._findings

    @property
    def target(self) -> str:
        return self._target

    @property
    def is_running(self) -> bool:
        return self._is_running
    
    @property
    def is_analysis_active(self) -> bool:
        """True if any analysis phase is running (scan, entity detection, or AI)."""
        return self._is_running or self._entity_detection_running
    
    @property
    def current_phase(self) -> str:
        """Current scan phase: idle, filesystem, entity_detection, ai_classification, complete, stopped."""
        return self._current_phase
    
    @property
    def has_entities(self) -> bool:
        """True if semantic entities are available."""
        return len(self._entities) > 0

    @property
    def total_count(self) -> int:
        return len(self._findings)

    @property
    def total_size(self) -> int:
        return self._total_size

    @property
    def total_size_str(self) -> str:
        return _format_size(self._total_size)

    @property
    def halted(self) -> bool:
        return self._halted

    @property
    def stopped(self) -> bool:
        return self._stopped

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def start_time(self) -> float:
        return self._start_time

    @property
    def known_paths(self) -> set:
        return self._known_paths

    @property
    def resume_frontier(self) -> list:
        """Directories a restored session hadn't walked yet (for continuation)."""
        return self._resume_frontier

    @property
    def scan_mode(self) -> str:
        return self._scan_mode

    @property
    def entities(self) -> list[SmartEntity]:
        return self._entities

    @property
    def entity_count(self) -> int:
        return len(self._entities)

    def add_entities(self, entities: list) -> None:
        """Append entities to the current list and refresh the UI.

        Called by DuplicateDetector when new duplicate groups are ready.
        Thread-safe: caller must emit via a queued signal or call from main thread.
        """
        if not entities:
            return
        self._entities.extend(entities)
        self._entity_dict_dirty = True
        self._invalidate_path_index()
        self.ui_refresh.emit()

    def remove_entities_by_path(self, paths: set) -> int:
        """Remove (or shrink) entities whose files were cleaned.

        Called after a successful Recycle Bin operation. An entity is dropped
        when its own path was cleaned OR when every file it stood for
        (``removable_file_paths`` — loose buckets, installer folders) is gone.
        A partially-cleaned bucket keeps the survivors so it doesn't vanish
        while files remain. Emits ui_refresh so connected screens update.
        """
        norm = {p.replace("\\", "/").lower().rstrip("/") for p in paths}
        norm_full = {p.replace("\\", "/").lower() for p in paths}
        before = len(self._entities)
        kept: list = []
        for e in self._entities:
            ep = e.path.replace("\\", "/").lower().rstrip("/")
            if ep in norm:
                continue  # the entity's own path was removed
            rfp = [p for p in getattr(e, "removable_file_paths", []) if p]
            if rfp:
                remaining = [
                    p for p in rfp
                    if p.replace("\\", "/").lower() not in norm_full
                ]
                if not remaining:
                    continue  # every file this entity represented is gone
                if len(remaining) != len(rfp):
                    # Partial cleanup: keep the entity but drop cleaned files.
                    e.removable_file_paths = remaining
                    e.file_count = len(remaining)
            kept.append(e)
        self._entities = kept
        removed = before - len(self._entities)
        # Always purge matching raw findings so counts/caches stay consistent.
        self._findings = [
            f for f in self._findings
            if f.path.replace("\\", "/").lower() not in norm_full
        ]
        if removed or norm_full:
            self._entity_dict_dirty = True
            self._dict_cache_dirty = True
            self._invalidate_path_index()
            self.ui_refresh.emit()
        return removed

    @property
    def ai_explainer(self):
        return self._ai_explainer

    # ── AI integration ───────────────────────────────────────

    def set_settings_store(self, store):
        self._settings_store = store

    def set_ai_explainer(self, explainer):
        """Attach an AIExplainer instance and wire its signals."""
        self._ai_explainer = explainer
        explainer.finding_updated.connect(self._on_ai_finding_updated)
        explainer.log_line.connect(self.log_line.emit)

    def _on_ai_finding_updated(self, finding):
        """Forward AIExplainer updates to the UI signal.

        Ignores stale results from a previous session by checking session_id
        on the AI explainer (if it carries one).
        """
        if self._ai_explainer and hasattr(self._ai_explainer, '_session_id'):
            if self._ai_explainer._session_id and self._ai_explainer._session_id != self._session_id:
                return  # stale result from old session
        self._dict_cache_dirty = True
        self._entity_dict_dirty = True
        self.ai_finding_updated.emit(finding)
        self.ui_refresh.emit()

    def start_ai_queue(self, run_mode: str = "new"):
        """Enqueue current findings/entities and start the AI explanation queue.
        
        Args:
            run_mode: "new" | "resume" | "restore" - controls AI cache behavior
        """
        if not self._ai_explainer or not self._settings_store:
            self.log_line.emit("[ai] unavailable · no AI explainer configured")
            return
        if not self._settings_store.get("ai_findings_enabled", True):
            self.log_line.emit("[ai] disabled · findings explanations off")
            return

        # Stamp session_id so stale results can be detected
        self._ai_explainer._session_id = self._session_id
        if self._scan_mode == "smart" and self._entities:
            count = len(self._entities)
            self.log_line.emit(f"[ai] enqueuing {count} entities for explanation")
            self._ai_explainer.enqueue_entities(list(self._entities))
        else:
            count = len(self._findings)
            self.log_line.emit(f"[ai] enqueuing {count} findings for explanation")
            self._ai_explainer.enqueue(list(self._findings))
        self._ai_explainer.start(run_mode=run_mode)

    def stop_ai_queue(self):
        """Cancel AI explanation queue."""
        if self._ai_explainer:
            self._ai_explainer.stop()

    # ── Lifecycle ────────────────────────────────────────────

    def set_scan_mode(self, mode: str):
        """Set scan mode: 'smart' or 'all'."""
        self._scan_mode = mode

    def set_scan_roots(self, roots: list):
        """Record the root(s) for this scan (one folder/drive, or many drives)."""
        self._scan_roots = [r for r in (roots or []) if r]

    @property
    def scan_roots(self) -> list:
        return list(self._scan_roots)

    def _findings_under(self, root: str) -> list:
        """Findings whose path lives under *root* (drive-partition for detection)."""
        rn = root.replace("\\", "/").lower().rstrip("/")
        return [f for f in self._findings
                if f.path.replace("\\", "/").lower().startswith(rn)]

    def clear(self):
        """Clear all findings and caches."""
        self.stop_ai_queue()
        self._findings.clear()
        self._dict_cache.clear()
        self._dict_cache_dirty = True
        self._entities.clear()
        self._entity_dict_cache.clear()
        self._entity_dict_dirty = True
        self._skipped_entries.clear()
        self._cat_counts.clear()
        self._cat_sizes.clear()
        self._risk_counts.clear()
        self._total_size = 0
        self._scan_frontier = []
        self._resume_frontier = []
        self._scan_roots = []
        self._last_full_checkpoint = 0.0
        self._path_index = None
        self._halted = False
        self._stopped = False
        self._pending_ui = False
        self._known_paths.clear()
        self._resume_baseline_count = 0
        self._session_id = str(uuid.uuid4())[:8]
        self._start_time = time.time()

    @property
    def skipped_entries(self) -> list[dict]:
        """Return the list of structured skipped/protected entries."""
        return list(self._skipped_entries)

    @property
    def skipped_count(self) -> int:
        return len(self._skipped_entries)

    def add_skipped_entries(self, entries: list[dict]):
        """Receive a batch of skipped entries from ScanWorker and emit update signal."""
        self._skipped_entries.extend(entries)
        self.skipped_entries_updated.emit()

    def set_running(self, running: bool, target: str = ""):
        self._is_running = running
        if running:
            self._target = target
            self._halted = False
            self._stopped = False
            self._entity_detection_cancelled = False
            self._entity_detection_running = False
            self._current_phase = "filesystem"
            self._throttle.start()
            self._autosave.start()
            self.scan_started.emit(target)
        else:
            self._throttle.stop()
            self._autosave.stop()
            self._flush_ui()  # final flush
            if self._stopped or self._halted:
                self.scan_halted.emit()
                # For large scans, serialize findings off the main thread to avoid
                # blocking the UI for 30+ seconds while writing a multi-GB JSON file.
                if len(self._findings) > _LARGE_SCAN_THRESHOLD:
                    self._save_session_background("stopped", lightweight=False)
                else:
                    self._save_session("stopped")
            else:
                self.scan_finished.emit()

                # In smart mode, run entity detection before AI queue.
                # Skip re-detection if entities were restored from session and
                # the continuation scan added no new findings.
                if self._scan_mode == "smart":
                    can_reuse = (
                        bool(self._entities)
                        and self._resume_baseline_count > 0
                        and len(self._findings) == self._resume_baseline_count
                    )
                    if can_reuse:
                        self._reuse_restored_entities()
                    else:
                        self._resume_baseline_count = 0
                        self._run_entity_detection()
                else:
                    # All-files mode: skip to complete
                    self._set_phase("complete", "analysis complete")

    def halt(self):
        """Mark scan as halted — partial results preserved."""
        self._halted = True

    def stop_all(self):
        """Full stop: cancel scan worker, entity detection, AI queue, mark stopped."""
        was_running = self._is_running
        self._stopped = True
        self._halted = True
        
        # Cancel entity detection if running
        if self._entity_detection_running:
            self._entity_detection_cancelled = True
            self._entity_detection_running = False
            self.log_line.emit("[smart] entity detection cancelled")
        
        self.stop_ai_queue()
        self.log_line.emit("[ai] queue cancelled")
        
        if was_running:
            self._set_phase("stopped", "analysis stopped by user")
        self.log_line.emit("[scan] stopped · partial results preserved")

    # ── Data ingestion ───────────────────────────────────────

    def add_findings(self, findings: list):
        """Add a batch of Finding objects. Incrementally updates caches."""
        for f in findings:
            norm = f.path.replace("\\", "/").lower()
            if norm in self._known_paths:
                continue
            self._known_paths.add(norm)
            self._findings.append(f)
            self._total_size += f.size_bytes

            # Category
            cat = f.category
            self._cat_counts[cat] = self._cat_counts.get(cat, 0) + 1
            self._cat_sizes[cat] = self._cat_sizes.get(cat, 0) + f.size_bytes

            # Risk
            risk = f.risk
            self._risk_counts[risk] = self._risk_counts.get(risk, 0) + 1

        self._dict_cache_dirty = True
        self._pending_ui = True
        self._invalidate_path_index()

    def find_by_path(self, path: str):
        """Return the live SmartEntity or Finding for *path*, or None.

        Used by on-demand "Ask AI" so the explainer mutates the same object the
        UI reads from. Backed by a lazily-built index — a linear scan over a
        million findings on every click would stall the UI. The index is only
        built when something actually asks, and dropped whenever the underlying
        collections change.
        """
        if not path:
            return None
        if self._path_index is None:
            idx: dict = {}
            for f in self._findings:
                idx[f.path.replace("\\", "/").lower()] = f
            # Entities take precedence over the raw finding at the same path.
            for e in self._entities:
                idx[e.path.replace("\\", "/").lower()] = e
            self._path_index = idx
        return self._path_index.get(path.replace("\\", "/").lower())

    def _invalidate_path_index(self):
        self._path_index = None

    def on_frontier_update(self, frontier: list):
        """Store the scanner's latest resume frontier (pending directories).

        Wired to ScanWorker.frontier_update. Kept in sync so periodic and final
        checkpoints can persist it — enabling a later resume to continue from
        here instead of re-walking the whole tree.
        """
        self._scan_frontier = list(frontier)

    # ── Throttled UI refresh ─────────────────────────────────

    def _flush_ui(self):
        if self._pending_ui:
            self._pending_ui = False
            self.ui_refresh.emit()

    # ── Accessors ────────────────────────────────────────────

    def findings_as_dicts(self) -> list[dict]:
        """Return findings as dicts, sorted by size descending. Cached.

        During active scan in Smart mode, returns empty list to avoid
        expensive serialization — the Findings screen should wait for
        entity detection to complete.
        """
        # During active scan in smart mode, don't serialize raw findings
        if self._is_running and self._scan_mode == "smart":
            return []

        if self._dict_cache_dirty:
            t0 = time.time()
            self._dict_cache = [f.to_dict() for f in self._findings]
            self._dict_cache.sort(key=lambda d: d["size_bytes"], reverse=True)
            self._dict_cache_dirty = False
            elapsed_ms = (time.time() - t0) * 1000
            if elapsed_ms > 200:
                self.log_line.emit(
                    f"[perf] findings_as_dicts: {len(self._findings):,} items in {elapsed_ms:.0f}ms"
                )
        return self._dict_cache

    def entities_as_dicts(self) -> list[dict]:
        """Return entities as dicts, sorted by size descending. Cached."""
        if self._entity_dict_dirty:
            t0 = time.time()
            self._entity_dict_cache = [e.to_dict() for e in self._entities]
            self._entity_dict_cache.sort(key=lambda d: d["size_bytes"], reverse=True)
            self._entity_dict_dirty = False
            elapsed_ms = (time.time() - t0) * 1000
            if elapsed_ms > 50:
                self.log_line.emit(
                    f"[perf] entities_as_dicts: {len(self._entities)} entities in {elapsed_ms:.0f}ms"
                )
        return self._entity_dict_cache

    def display_items(self) -> list[dict]:
        """Return the appropriate items for the current scan mode."""
        if self._scan_mode == "smart" and self._entities:
            return self.entities_as_dicts()
        return self.findings_as_dicts()

    def findings_for_category(self, category: str, limit: int = 5_000) -> tuple:
        """Return (capped_dicts, total_count) for a category without full serialization.

        Iterates _findings directly so only matched items are converted to dicts.
        Safe to call on 2M+ finding sets where findings_as_dicts() would OOM.
        """
        if self._scan_mode == "smart" and self._entities:
            items = self.entities_as_dicts()
            cat_norm = category.strip().title()
            matched = [e for e in items if (e.get("category") or "Unknown").strip().title() == cat_norm]
            return matched[:limit], len(matched)

        cat_norm = category.strip().title()
        matched_dicts: list = []
        total = 0
        for f in self._findings:
            f_cat = (f.category or "Unknown").strip().title()
            if f_cat == cat_norm:
                total += 1
                if len(matched_dicts) < limit:
                    matched_dicts.append(f.to_dict())
        return matched_dicts, total

    def display_count(self) -> int:
        """Return the count of display items for the current mode."""
        if self._scan_mode == "smart" and self._entities:
            return len(self._entities)
        return len(self._findings)

    def category_summary(self) -> dict:
        """Return {category: {"count": int, "size_bytes": int}}."""
        if self._scan_mode == "smart" and self._entities:
            result = {}
            for e in self._entities:
                cat = e.category
                if cat not in result:
                    result[cat] = {"count": 0, "size_bytes": 0}
                result[cat]["count"] += 1
                result[cat]["size_bytes"] += e.size_bytes
            return result
        result = {}
        for cat in self._cat_counts:
            result[cat] = {
                "count": self._cat_counts[cat],
                "size_bytes": self._cat_sizes.get(cat, 0),
            }
        return result

    def risk_summary(self) -> dict:
        """Return {risk: count}."""
        if self._scan_mode == "smart" and self._entities:
            counts = {}
            for e in self._entities:
                counts[e.risk] = counts.get(e.risk, 0) + 1
            return counts
        return dict(self._risk_counts)

    # ── Smart entity detection ────────────────────────────────

    def _set_phase(self, phase: str, message: str = ""):
        """Set current scan phase and emit signal."""
        self._current_phase = phase
        self.scan_phase_changed.emit(phase, message)
        if message:
            self.log_line.emit(f"[scan] {phase}: {message}")

    def _reuse_restored_entities(self):
        """Skip entity detection when no new files arrived during a resume.

        The continuation scan deduped against known_paths, so if total_count
        equals the pre-restore baseline the filesystem is unchanged.  The
        entities loaded by restore_from_session are still valid — emit
        entities_ready so the Findings screen refreshes without re-detecting.
        """
        n = len(self._entities)
        self._resume_baseline_count = 0
        self._entity_detection_running = False
        self._entity_dict_dirty = True
        self.log_line.emit(
            f"[smart] resume: no new files found · reusing {n} restored entities"
        )
        self._set_phase("complete", f"{n} entities · resumed from session")
        self.entities_ready.emit()

    def _run_entity_detection(self):
        """Run entity detection in a background thread to avoid blocking the UI."""
        if self._entity_detection_cancelled:
            return
        
        self._entity_detection_running = True
        self._set_phase("entity_detection", "grouping storage into semantic entities…")
        self.log_line.emit("[smart] starting entity detection in background…")
        
        t = threading.Thread(target=self._entity_detection_worker, daemon=True)
        t.start()

    def _entity_detection_worker(self):
        """Background worker for entity detection — runs off UI thread."""
        from app.services.entity_detector import detect_entities

        def progress_fn(phase: str, grouped_files: int, ungrouped_files: int, entities_created: int, coverage_pct: int = 0):
            """Emit progress updates via signal for UI consumption."""
            self.entity_progress.emit(phase, grouped_files, ungrouped_files, entities_created, coverage_pct)
        
        # Track entities discovered (for AI queue after grouping complete)
        discovered_entities: list = []
        
        def entity_fn(ent):
            """Track entity as it's discovered (AI starts only after grouping complete)."""
            discovered_entities.append(ent)

        try:
            t0 = time.time()
            _extra_monoliths = []
            if self._settings_store:
                _extra_monoliths = self._settings_store.get(
                    "scan.monolith_patterns", []
                ) or []

            # The detector groups relative to a single root (depth is computed
            # as path-minus-root). A multi-drive scan therefore can't be fed one
            # root — D:/ paths wouldn't start with C:/ and would mis-group. Run
            # detection once per drive over that drive's findings, then merge;
            # drives are independent installs, so this is also the correct split.
            roots = [r for r in self._scan_roots if r] or [self._target]
            if len(roots) > 1:
                entities = []
                for root in roots:
                    if self._entity_detection_cancelled:
                        break
                    subset = self._findings_under(root)
                    if not subset:
                        continue
                    self.log_line.emit(
                        f"[smart] grouping {len(subset):,} items under {root}"
                    )
                    entities.extend(detect_entities(
                        subset, root,
                        log_fn=lambda msg: self.log_line.emit(msg),
                        progress_fn=progress_fn,
                        entity_fn=entity_fn,
                        extra_monolith_patterns=_extra_monoliths,
                    ))
            else:
                entities = detect_entities(
                    self._findings,
                    roots[0],
                    log_fn=lambda msg: self.log_line.emit(msg),
                    progress_fn=progress_fn,
                    entity_fn=entity_fn,
                    extra_monolith_patterns=_extra_monoliths,
                )

            elapsed = time.time() - t0
            if elapsed > 0.05:
                self.log_line.emit(f"[perf] entity detection: {elapsed*1000:.1f}ms")
            
            if self._entity_detection_cancelled:
                self.log_line.emit("[smart] entity detection cancelled")
                return
            
            # CRITICAL: Deliver results to main thread via signal (NOT QTimer from worker!)
            # QueuedConnection ensures _on_entity_results_delivered runs on main thread
            _log.debug(f"[thread] entity worker finished · emitting results signal · {len(entities)} entities")
            self._entity_results_internal.emit(entities, discovered_entities)
            
        except Exception as e:
            self.log_line.emit(f"[smart] entity detection error: {e}")
            import traceback
            self.log_line.emit(f"[smart] traceback: {traceback.format_exc()}")
            # Emit error state to main thread
            self._entity_results_internal.emit([], [])

    def _on_entity_results_delivered(self, entities: list, discovered_entities: list):
        """Slot for _entity_results_internal signal — runs on main thread.
        
        This receives entity results from the worker thread via queued signal,
        then stores them and applies to ScanState on the main thread.
        """
        import threading
        current_thread = threading.current_thread().name
        _log.debug(f"[thread] _on_entity_results_delivered on: {current_thread}")
        
        # Store the entities on the main thread (safe for UI access)
        self._pending_entities = entities
        
        _log.debug(f"[thread] entities delivered to main thread · {len(entities)} entities")
        
        # Now apply results (will emit entities_ready on main thread)
        self._apply_entity_results()

    def _apply_entity_results(self):
        """Apply entity detection results on the main thread.
        
        LIFECYCLE: entity detection complete → entities stored → entities_ready emitted
                  → Findings refreshes → AI queue starts (independent of UI)
        """
        import threading
        import traceback
        current_thread = threading.current_thread().name
        
        try:
            _log.debug("[smart] ========== _apply_entity_results START ==========")
            _log.debug(f"[thread] _apply_entity_results on: {current_thread}")
            _log.debug("[smart] [LIFECYCLE] Step 1: Entity detection results on main thread")
            
            if self._entity_detection_cancelled:
                self.log_line.emit("[smart] entity detection was cancelled, aborting")
                self._entity_detection_running = False
                return
                
            entities = getattr(self, "_pending_entities", None)
            if entities is None:
                self.log_line.emit("[smart] ERROR: _pending_entities is None - CRITICAL BUG")
                return
            
            n_entities = len(entities)
            _log.debug(f"[smart] [LIFECYCLE] Step 2: Received {n_entities} entities from detector")
            
            if n_entities == 0:
                self.log_line.emit("[smart] ERROR: no entities received, showing empty state")
                self._entity_detection_running = False
                self._set_phase("complete", "no entities found")
                _log.debug("[smart] [LIFECYCLE] Step 8: Emitting entities_ready (empty)")
                self.entities_ready.emit()
                _log.debug("[smart] [LIFECYCLE] Step 9: entities_ready emitted (empty)")
                return
            
            # Resume: carry AI explanations from the previously restored entities
            # onto freshly re-detected ones (matched by stable cache_key) so we
            # don't re-run the LLM for entities that didn't change. This
            # complements the on-disk AI cache and also covers the case where a
            # run was interrupted before that cache was ever written.
            if self._run_mode == "resume" and self._entities:
                prior = {e.cache_key: e for e in self._entities}
                carried = 0
                for e in entities:
                    old = prior.get(e.cache_key)
                    if (old and getattr(old, "ai_explanation", "")
                            and e.ai_status in ("none", "pending", "")):
                        e.ai_status = old.ai_status or "ready"
                        e.ai_explanation = old.ai_explanation
                        e.ai_model = old.ai_model
                        e.ai_language = old.ai_language
                        e.ai_error = old.ai_error
                        carried += 1
                if carried:
                    self.log_line.emit(
                        f"[smart] resume: reused AI explanations for {carried} "
                        f"unchanged entities"
                    )

            # Store entities
            _log.debug("[smart] [LIFECYCLE] Step 3: Storing semantic entities to ScanState...")
            self._entities = entities
            self._pending_entities = None
            self._entity_dict_dirty = True
            self._invalidate_path_index()
            self._entity_detection_running = False
            
            total_size = sum(e.size_bytes for e in entities)
            _log.debug(f"[smart] [LIFECYCLE] Step 4: Entities committed to ScanState · {n_entities} entities · {_human_size(total_size)}")
            
            # AI explains only entities that are visible in Findings (post-processed list)
            explainable = [e for e in entities if getattr(e, 'risk', '') not in ('Protected', 'Safe')]

            _log.debug(f"[ai] [LIFECYCLE] Step 5: Preparing AI queue...")
            _log.debug(f"[ai]    Total entities: {len(entities)}")
            _log.debug(f"[ai]    Explainable entities: {len(explainable)}")
            
            # AI starts only after grouping is complete
            ai_will_start = False
            skip_reason = None
            
            if not self._ai_explainer:
                skip_reason = "AI explainer not configured"
            elif not self._settings_store:
                skip_reason = "settings not loaded"
            elif not self._settings_store.get("ai_findings_enabled", True):
                skip_reason = "findings explanations disabled"
            elif not explainable:
                skip_reason = "no explainable entities"
            elif self._ai_explainer.is_running:
                skip_reason = "queue already running"
            else:
                ai_will_start = True
            
            if ai_will_start:
                model = self._settings_store.get("ai_model", "unknown")
                lang = self._settings_store.get("ai_language", "english")
                _log.debug(f"[ai] [LIFECYCLE] Step 6a: Starting AI classification")
                _log.debug(f"[ai]    Queue size: {len(explainable)} entities")
                _log.debug(f"[ai]    Model: {model}")
                _log.debug(f"[ai]    Language: {lang}")
                self._set_phase("ai_classification", f"{n_entities} entities · AI starting...")
                # Enqueue entities and start AI queue with run_mode
                self._ai_explainer.enqueue_entities(explainable)
                self._ai_explainer.start(run_mode=self._run_mode)
                _log.debug("[ai] [LIFECYCLE] Step 6b: AI queue started successfully")
            else:
                _log.debug(f"[ai] [LIFECYCLE] Step 6a: AI skipped · {skip_reason}")
                self._set_phase("complete", f"{n_entities} entities · {skip_reason}")
            
            # CRITICAL: Emit entities_ready to trigger Findings dashboard refresh
            _log.debug("[smart] [LIFECYCLE] Step 7: Emitting entities_ready signal...")
            # Note: receivers() is not available in PySide6 SignalInstance
            _log.debug(f"[thread] entities_ready emit on: {current_thread}")
            self.entities_ready.emit()
            _log.debug("[smart] [LIFECYCLE] Step 8: entities_ready signal emitted SUCCESSFULLY")
            _log.debug(f"[thread] ScanState now has {self.entity_count} entities")
            _log.debug("[smart] ========== _apply_entity_results COMPLETE ==========")
            
        except Exception as e:
            # CRITICAL: Never silently fail — always log and set error state
            error_msg = f"[smart] ERROR during entity commit: {e}"
            self.log_line.emit(error_msg)
            self.log_line.emit(f"[smart] traceback: {traceback.format_exc()}")
            
            # Set phase to error so UI knows something went wrong
            self._set_phase("error", f"entity commit failed: {e}")
            
            # Still emit entities_ready with whatever state we have so UI isn't stuck
            # This prevents the loading screen from hanging forever
            _log.debug("[smart] [LIFECYCLE] ERROR: Emitting entities_ready despite error to unblock UI")
            self.entities_ready.emit()

    # ── Session persistence ──────────────────────────────────

    def _aggregates_snapshot(self) -> dict:
        """Copy the category/risk/size aggregates for a snapshot.

        Must be called on the thread that owns the aggregates (the main thread,
        where add_findings mutates them). A background save that iterated the
        live dicts would raise "dictionary changed size during iteration" the
        moment the scanner recorded a new category mid-save — and the save
        thread swallows exceptions, so the autosave would silently do nothing.
        """
        return {
            "risk_counts": dict(self._risk_counts),
            "cat_totals": {c: {"count": n, "size_bytes": self._cat_sizes.get(c, 0)}
                           for c, n in list(self._cat_counts.items())},
            "total_size": self._total_size,
        }

    def _build_snapshot(self, status: str, lightweight: bool = False,
                        findings: list = None, entities: list = None,
                        frontier: list = None, aggregates: dict = None) -> dict:
        from app.state.session_store import build_snapshot

        # Callers on a background thread pass stable list snapshots so we never
        # touch the live lists (which the scanner is mutating) here.
        findings = self._findings if findings is None else findings
        entities = self._entities if entities is None else entities
        frontier = self._scan_frontier if frontier is None else frontier
        if aggregates is None:
            aggregates = self._aggregates_snapshot()

        # For lightweight (mid-scan) saves, skip serializing all raw findings
        # to avoid expensive serialization. Full findings only on final save.
        if lightweight and len(findings) > _LARGE_SCAN_THRESHOLD:
            findings_dicts = []
            entities_dicts = []
        else:
            findings_dicts = [f.to_dict() for f in findings]
            entities_dicts = [e.to_dict() for e in entities]

        # Use entity-based aggregates when semantic entities are available —
        # raw _risk_counts and _cat_counts reflect individual files, not groups.
        if entities:
            e_risk: dict[str, int] = {}
            e_cat: dict[str, dict] = {}
            for e in entities:
                e_risk[e.risk] = e_risk.get(e.risk, 0) + 1
                cat = e.category
                if cat not in e_cat:
                    e_cat[cat] = {"count": 0, "size_bytes": 0}
                e_cat[cat]["count"] += 1
                e_cat[cat]["size_bytes"] += e.size_bytes
            risk_totals = e_risk
            cat_totals = e_cat
        else:
            risk_totals = aggregates["risk_counts"]
            cat_totals = aggregates["cat_totals"]

        return build_snapshot(
            session_id=self._session_id,
            target=self._target,
            scan_mode=self._scan_mode,
            status=status,
            start_time=self._start_time,
            scanned_count=len(findings),
            total_size=aggregates["total_size"],
            category_totals=cat_totals,
            risk_totals=risk_totals,
            findings_dicts=findings_dicts,
            entities_dicts=entities_dicts,
            scan_frontier=frontier,
        )

    def _save_session(self, status: str):
        from app.state.session_store import save_session, append_to_history
        try:
            data = self._build_snapshot(status)
            save_session(data)
            if status in ("completed", "stopped"):
                append_to_history(data)
        except Exception:
            pass  # best-effort

    def _autosave_session(self):
        """Periodic auto-save during active scan — always off the UI thread.

        Always a lightweight save. For a SMALL scan that still serializes full
        findings (cheap). For a LARGE scan it writes only the frontier + summary
        and skips the raw findings — re-encoding ~1M+ findings to JSON on a timer
        starves the UI thread through the GIL even when done in a worker, causing
        a multi-second freeze every couple of minutes.

        Full findings for a large scan are written at the moments that matter —
        on Stop, on app close, and on completion (see set_running / _shutdown /
        save_session_final) — where a brief pause is expected. A hard crash
        mid-scan loses the in-progress findings, but the filesystem walk is fast
        (scandir) so re-running is cheap; the frontier still lets an intentional
        Stop/close resume without re-walking.
        """
        if not self._is_running:
            return
        self._save_session_background("running", lightweight=True)

    def _save_session_background(self, status: str, lightweight: bool = True,
                                 append_history: bool = False):
        """Save session in a background thread to avoid blocking UI.

        Snapshots the findings/entities list references on the *caller's* thread
        (a GIL-atomic ``list()`` copy) so the worker serializes a stable view
        and never races with the scanner appending findings on the main thread.
        """
        findings_snap = list(self._findings)
        entities_snap = list(self._entities)
        frontier_snap = list(self._scan_frontier)
        aggregates_snap = self._aggregates_snapshot()

        def _bg_save():
            try:
                from app.state.session_store import save_session, append_to_history
                data = self._build_snapshot(status, lightweight=lightweight,
                                            findings=findings_snap,
                                            entities=entities_snap,
                                            frontier=frontier_snap,
                                            aggregates=aggregates_snap)
                save_session(data)
                if append_history and status in ("completed", "stopped"):
                    append_to_history(data)
            except Exception:
                _log.exception("[session] background save failed")
            finally:
                with self._save_lock:
                    self._save_threads.discard(threading.current_thread())

        t = threading.Thread(target=_bg_save, daemon=True)
        with self._save_lock:
            self._save_threads.add(t)
        t.start()

    def wait_for_saves(self, timeout: float = 5.0) -> bool:
        """Block until in-flight background saves finish. Returns True if all did.

        Called from shutdown: these are daemon threads, so without this the
        interpreter would kill a running save on exit and drop the session the
        user just stopped. Bounded so a stuck write can't hang the close.
        """
        deadline = time.monotonic() + timeout
        with self._save_lock:
            pending = list(self._save_threads)
        for t in pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            t.join(remaining)
        return not any(t.is_alive() for t in pending)

    def save_session_final(self, status: str = "completed",
                           background_large: bool = False):
        """Save session on scan complete or app close. Called externally.

        On scan completion (background_large=True) a big scan is written off the
        UI thread so the app doesn't freeze while serialising a large snapshot
        twice. App-close keeps the default synchronous save so it finishes
        before the process exits.
        """
        if background_large and len(self._findings) > _LARGE_SCAN_THRESHOLD:
            self._save_session_background(status, lightweight=False, append_history=True)
        else:
            self._save_session(status)

    def restore_from_session(self, data: dict):
        """Restore findings/entities from a saved session snapshot.

        Populates findings list, known_paths, aggregation caches, and
        entities from the saved dicts.  After calling this, the UI can
        show restored findings immediately.
        """
        self._session_id = data.get("session_id", str(uuid.uuid4())[:8])
        self._target = data.get("target", "")
        self._scan_mode = data.get("scan_mode", "smart")
        self._start_time = data.get("start_time", time.time())
        # Directories the interrupted scan never reached — handed to the next
        # ScanWorker so it continues from here instead of re-walking everything.
        self._resume_frontier = list(data.get("scan_frontier", []) or [])

        # Restore findings
        for fd in data.get("findings", []):
            try:
                f = Finding(
                    path=fd["path"],
                    name=fd["name"],
                    is_dir=fd.get("is_dir", False),
                    size_bytes=fd.get("size_bytes", 0),
                    extension=fd.get("name", "").rsplit(".", 1)[-1] if "." in fd.get("name", "") else "",
                    # Carry the real mtime across a resume. Hardcoding 0.0 here
                    # made every restored finding look ~55 years old and broke
                    # age-based sorting for the whole restored session.
                    modified=fd.get("modified", 0.0) or 0.0,
                    accessed=0.0,
                    parent=fd.get("path", "").rsplit("/", 1)[0].rsplit("\\", 1)[0],
                    category=fd.get("category", ""),
                    risk=fd.get("risk", ""),
                    source_rule=fd.get("source_rule", ""),
                    risk_reason=fd.get("risk_reason", ""),
                    size=fd.get("size", ""),
                    age=fd.get("age", ""),
                    ai_status=fd.get("ai_status", "none"),
                    ai_explanation=fd.get("ai_explanation", ""),
                    ai_error=fd.get("ai_error", ""),
                    ai_model=fd.get("ai_model", ""),
                    ai_language=fd.get("ai_language", ""),
                    cloud_only=fd.get("cloud_only", False),
                )
                norm = f.path.replace("\\", "/").lower()
                self._known_paths.add(norm)
                self._findings.append(f)
                self._total_size += f.size_bytes

                cat = f.category
                self._cat_counts[cat] = self._cat_counts.get(cat, 0) + 1
                self._cat_sizes[cat] = self._cat_sizes.get(cat, 0) + f.size_bytes
                risk = f.risk
                self._risk_counts[risk] = self._risk_counts.get(risk, 0) + 1
            except (KeyError, TypeError):
                continue

        # Restore entities (for smart mode)
        for ed in data.get("entities", []):
            try:
                e = SmartEntity(
                    path=ed["path"],
                    name=ed.get("name", ed["path"]),
                    entity_type=ed.get("entity_type", "unknown_folder"),
                    size_bytes=ed.get("size_bytes", 0),
                    file_count=ed.get("file_count", 0),
                    folder_count=ed.get("folder_count", 0),
                    risk=ed.get("risk", ""),
                    risk_reason=ed.get("risk_reason", ""),
                    confidence=ed.get("confidence", "heuristic"),
                    ai_status=ed.get("ai_status", "none"),
                    ai_explanation=ed.get("ai_explanation", ""),
                    ai_error=ed.get("ai_error", ""),
                    ai_model=ed.get("ai_model", ""),
                    ai_language=ed.get("ai_language", ""),
                    summary=ed.get("summary", ""),
                    cloud_sync_provider=ed.get("cloud_sync_provider", ""),
                    modified=ed.get("modified", 0.0),
                    age_boost=ed.get("age_boost", 0.0),
                    dup_reclaimable=ed.get("dup_reclaimable", 0),
                    children_sample=ed.get("children_sample", []),
                    duplicate_locations=ed.get("duplicate_locations", []),
                    removable_duplicate_paths=ed.get("removable_duplicate_paths", []),
                )
                self._entities.append(e)
            except (KeyError, TypeError):
                continue

        # Record baseline so a continuation scan can detect "no new files"
        # and skip redundant entity re-detection (see set_running / _reuse_restored_entities).
        self._resume_baseline_count = len(self._findings)

        self._dict_cache_dirty = True
        self._entity_dict_dirty = True
        self._invalidate_path_index()
        self.ui_refresh.emit()

        # Emit entities_ready so Findings screen refreshes with restored data
        # This is needed when opening Findings from Home (Open Findings button)
        if self._entities or self._findings:
            self.entities_ready.emit()
