"""AI explanation queue — background workers for local Ollama explanations.

Runs alongside/after scanning. Prioritises Review > Optional > Unknown large > Safe.
Respects all settings: enabled, risky-only, concurrency, timeout, caching.
Emits Qt signals so the UI can update live without blocking.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Optional

# ── Global AI serialization lock ────────────────────────────────────
# Ensures only one AI analysis job (Findings or Startups) runs at a time.
# Both AIExplainer._dispatcher() and StartupAIWorker.run() acquire this lock
# before sending requests to Ollama, preventing concurrent overload.
_AI_GLOBAL_LOCK = threading.Lock()

from PySide6.QtCore import QObject, Signal

from app.models.finding import Finding
from app.models.smart_entity import SmartEntity
from app.services.ollama_client import generate
from app.services.prompt_builder import build_prompt, build_entity_prompt


# Low temperature keeps factual explanations consistent — the default 0.8 makes
# small models ramble and invent product details.
#
# We deliberately do NOT cap num_predict. A thinking model (e.g. gemma4:e2b)
# spends output tokens on internal reasoning before the answer — capping to a
# few hundred tokens made it hit the limit mid-thought and return an EMPTY
# response (done_reason="length"), which surfaces as a failed explanation. The
# prompt controls answer length; the emergency request timeout bounds runaways.
def _gen_options(length: str) -> dict:
    return {"temperature": 0.2}


# ── Priority helpers ────────────────────────────────────────────

_RISK_PRIORITY = {
    "Review": 0,
    "Optional": 1,
    "Unknown": 2,
    "Safe": 3,
    "Protected": 99,  # never explain protected items
}


def _sort_key(item) -> tuple:
    """(risk_priority, -size) — higher-attention statuses first, larger items first."""
    risk = getattr(item, 'risk', 'Review')
    size = getattr(item, 'size_bytes', 0)
    return (_RISK_PRIORITY.get(risk, 2), -size)


# ── Disk cache ──────────────────────────────────────────────────

def _cache_dir() -> Path:
    appdata = os.environ.get("LOCALAPPDATA", "")
    if appdata:
        return Path(appdata) / "Podbye" / "cache" / "ai"
    return Path.home() / ".cache" / "podbye" / "ai"


def cache_dir() -> Path:
    """Public accessor for the AI explanation cache location.

    Settings/About reports and clears this directory, and must read the real
    path rather than restate a literal.
    """
    return _cache_dir()


def clear_cache() -> int:
    """Delete every cached explanation. Returns the number of files removed.

    Cached answers are keyed by finding + model + tone + length + language, so
    they are pure derived data: clearing costs a re-run, never a loss.
    """
    removed = 0
    directory = _cache_dir()
    if not directory.is_dir():
        return 0
    for entry in directory.glob("*.json"):
        try:
            entry.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def _cache_key_hash(finding: Finding, model: str, tone: str, length: str, language: str = "English") -> str:
    raw = f"{finding.cache_key}|{model}|{tone}|{length}|{language}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _load_cached(finding: Finding, model: str, tone: str, length: str, language: str = "English") -> Optional[str]:
    h = _cache_key_hash(finding, model, tone, length, language)
    p = _cache_dir() / f"{h}.json"
    if p.exists():
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("explanation", None)
        except Exception:
            pass
    return None


def _save_cached(finding: Finding, model: str, tone: str, length: str, language: str, explanation: str):
    h = _cache_key_hash(finding, model, tone, length, language)
    d = _cache_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{h}.json"
        with open(p, "w", encoding="utf-8") as f:
            json.dump({
                "path": finding.path,
                "model": model,
                "tone": tone,
                "length": length,
                "language": language,
                "explanation": explanation,
                "ts": time.time(),
            }, f)
    except OSError:
        pass


# ── AI Explainer (QObject with signals) ────────────────────────

class AIExplainer(QObject):
    """Background AI explanation queue.

    Signals:
        finding_updated(Finding) — emitted on the *main thread* when a Finding
            has its AI fields set (done, error, or disabled).
        queue_started() — queue processing has begun.
        queue_finished() — all eligible items processed.
        log_line(str) — operator-feed-style log messages.
    """

    finding_updated = Signal(object)   # Finding or SmartEntity
    queue_started = Signal()
    queue_finished = Signal()
    queue_progress = Signal(int, int, int, int)  # done, total, active, failed
    log_line = Signal(str)

    def __init__(self, settings_store, parent=None):
        super().__init__(parent)
        self._store = settings_store
        self._queue: list = []  # Finding or SmartEntity
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._active_count = 0
        self._active_lock = threading.Lock()
        self._running = False
        self._total_queued = 0
        self._total_done = 0
        self._total_failed = 0
        self._session_id: str = ""  # set by ScanState for stale-result protection
        self._run_mode: str = "new"  # "new" | "resume" | "restore"

    # ── Public API ──────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def total_failed(self) -> int:
        return self._total_failed

    def enqueue(self, findings: list):
        """Add findings to the explanation queue (called from main thread)."""
        if not self._store.get("ai_findings_enabled", True):
            return

        risky_only = self._store.get("ai_explain_risky_only")
        eligible = []
        for f in findings:
            if f.ai_status in ("ready", "done", "analyzing", "running", "pending"):
                continue
            # Never explain protected items
            if f.risk == "Protected":
                f.ai_status = "disabled"
                continue
            if risky_only and f.risk == "Safe":
                f.ai_status = "disabled"
                continue
            f.ai_status = "pending"
            eligible.append(f)

        if not eligible:
            return

        eligible.sort(key=_sort_key)
        with self._lock:
            self._queue.extend(eligible)
            self._total_queued += len(eligible)

    def enqueue_entities(self, entities: list):
        """Add SmartEntities to the explanation queue (Smart mode)."""
        if not self._store.get("ai_findings_enabled", True):
            return

        risky_only = self._store.get("ai_explain_risky_only")
        eligible = []
        for e in entities:
            if e.ai_status in ("ready", "done", "analyzing", "running", "pending"):
                continue
            # Never explain protected entities
            if e.risk == "Protected":
                e.ai_status = "disabled"
                continue
            if risky_only and e.risk == "Safe":
                e.ai_status = "disabled"
                continue
            e.ai_status = "pending"
            eligible.append(e)

        if not eligible:
            return

        # Sort entities: Risk first, then by size descending
        eligible.sort(key=_sort_key)
        with self._lock:
            self._queue.extend(eligible)
            self._total_queued += len(eligible)

    def start(self, run_mode: str = "new", force: bool = False):
        """Begin processing the queue with background threads.

        Args:
            run_mode: "new" | "resume" | "restore"
                - "new": Fresh analysis run, reset all state
                - "resume": Continuing interrupted run, preserve/restore state
                - "restore": Opening completed findings, use cache freely
            force: when True, ignore the global 'ai_findings_enabled' toggle
                (used by on-demand single-item "Ask AI" requests).
        """
        if self._running:
            return
        if not force and not self._store.get("ai_findings_enabled", True):
            self.log_line.emit("[ai] disabled · findings explanations off")
            return

        model = self._store.get("ai_model", "")
        if not model:
            self.log_line.emit("[ai] unavailable · no model selected")
            return

        # Store run mode for cache behavior control
        self._run_mode = run_mode

        self._cancel.clear()
        self._running = True
        
        # For new runs, explicitly reset counters to 0
        if run_mode == "new":
            self._total_done = 0
            self._total_failed = 0
            # Clear any inherited AI state from previous runs
            self._clear_inherited_state()
        
        with self._lock:
            self._total_queued = len(self._queue)

        if self._total_queued == 0:
            self._running = False
            self.log_line.emit("[ai] disabled · no eligible items to explain")
            return

        self.queue_started.emit()

        # Log active configuration for diagnostics
        lang = self._store.get('ai_explanation_language', 'English')
        conc = self._store.get('ai_max_concurrent', 3)
        
        mode_label = {"new": "fresh analysis", "resume": "resume", "restore": "restore"}.get(run_mode, run_mode)
        self.log_line.emit(f"[ai] queue started ({mode_label}) \u00b7 {self._total_queued} items")
        self.log_line.emit(f"[ai] model: {model} \u00b7 language: {lang} \u00b7 concurrency: {conc}")

        # Spawn dispatcher thread
        t = threading.Thread(target=self._dispatcher, daemon=True)
        t.start()

    def stop(self):
        """Cancel all pending work."""
        self._cancel.set()
        self._running = False
        self.log_line.emit("[ai] queue stopped")

    def _clear_inherited_state(self):
        """Clear any inherited AI state from previous runs.
        
        Called at the start of a fresh analysis to ensure counters
        and queue state represent only the current run.
        """
        with self._lock:
            # Reset all queued items to pending state
            for item in self._queue:
                if hasattr(item, 'ai_status'):
                    # Preserve 'disabled' (Protected items), reset others
                    if item.ai_status != "disabled":
                        item.ai_status = "pending"
                        item.ai_explanation = ""
                        item.ai_error = ""
                        item.ai_model = ""
            self._total_done = 0
            self._total_failed = 0

    def explain_item(self, item, force_refresh: bool = False) -> str:
        """Explain one Finding/SmartEntity on demand (user clicked "Ask AI").

        *force_refresh* is "Ask again": skip the stored answer and generate a
        new one, then write it over the old cache entry. Without it a re-ask
        returns the same text instantly, which is right for reopening a result
        and useless for regenerating one.

        Works for both scan modes and deliberately bypasses the global
        'ai_findings_enabled' toggle and the risky-only filter — the user
        explicitly asked about this specific item. The on-disk cache is reused
        (run_mode="restore") so a previously-seen item answers instantly.

        Returns "" when the request was queued, or a short reason code the
        caller can surface to the user:
            "no-model"  — no AI model configured in Settings
        """
        model = self._store.get("ai_model", "")
        if not model:
            self.log_line.emit("[ai] cannot explain · no model selected")
            return "no-model"

        item.ai_status = "pending"
        item.ai_explanation = ""
        item.ai_error = ""
        # Read by the worker and cleared there, so a forced re-ask does not
        # make every later pass over the same item bypass the cache too.
        if force_refresh:
            item.ai_force_refresh = True
        with self._lock:
            self._queue.insert(0, item)  # front of queue — the user is waiting
            self._total_queued += 1
        name = getattr(item, "name", "item")
        self.log_line.emit(f"[ai] ask · {name}")
        if not self._running:
            # force=True so this runs even when bulk explanations are disabled.
            self.start(run_mode="restore", force=True)
        return ""

    # ── Internals ───────────────────────────────────────────

    def _dispatcher(self):
        """Dispatcher loop — spawns up to max_concurrent worker threads."""
        # Acquire the global AI slot — only one AI job (Findings queue, Startups,
        # or a manual "Ask AI") runs at a time. Wait in short cancellable steps
        # rather than giving up: a long Startups run can legitimately hold the
        # slot for many minutes, and dropping the whole queue after a fixed
        # timeout meant those items silently never got explained.
        waited = 0.0
        _MAX_WAIT = 1800.0  # 30-min safety cap for a genuinely stuck holder
        while not self._cancel.is_set() and waited < _MAX_WAIT:
            if _AI_GLOBAL_LOCK.acquire(timeout=1.0):
                break
            waited += 1.0
            if waited == 5.0:  # only announce once, if we actually have to wait
                self.log_line.emit("[ai] waiting for another AI job to finish…")
        else:
            # Cancelled, or hit the safety cap without ever getting the slot.
            if waited >= _MAX_WAIT:
                self.log_line.emit("[ai] gave up waiting for the AI slot — run AI again to retry")
            self._running = False
            return
        if self._cancel.is_set():
            _AI_GLOBAL_LOCK.release()
            self._running = False
            return

        try:
            max_conc = self._store.get("ai_max_concurrent", 3)
            sem = threading.Semaphore(max_conc)

            while not self._cancel.is_set():
                with self._lock:
                    if not self._queue:
                        break
                    item = self._queue.pop(0)

                if item.ai_status == "disabled":
                    continue

                sem.acquire()
                if self._cancel.is_set():
                    sem.release()
                    break

                with self._active_lock:
                    self._active_count += 1

                t = threading.Thread(
                    target=self._process_one,
                    args=(item, sem),
                    daemon=True,
                )
                t.start()

            # Wait for all active workers to finish
            for _ in range(300):  # 30s max wait
                with self._active_lock:
                    if self._active_count <= 0:
                        break
                time.sleep(0.1)

        finally:
            _AI_GLOBAL_LOCK.release()

        self._running = False
        try:
            self.queue_finished.emit()
        except RuntimeError:
            pass
        self.log_line.emit(
            f"[ai] queue finished — {self._total_done} explained, "
            f"{self._total_failed} failed, "
            f"{self._total_queued - self._total_done - self._total_failed} not run"
        )

    def _process_one(self, item, sem: threading.Semaphore):
        """Process a single finding or entity in a worker thread."""
        try:
            if isinstance(item, SmartEntity):
                self._explain_entity(item)
            else:
                self._explain(item)
        finally:
            sem.release()
            with self._active_lock:
                self._active_count -= 1
            # Emit progress telemetry
            try:
                self.queue_progress.emit(
                    self._total_done, self._total_queued,
                    self._active_count, self._total_failed
                )
            except RuntimeError:
                pass

    def _explain(self, finding: Finding):
        """Run the actual explanation for one finding."""
        if self._cancel.is_set():
            return

        endpoint = self._store.get("ai_endpoint")
        model = self._store.get("ai_model", "")
        tone = self._store.get("ai_tone", "neutral")
        length = self._store.get("ai_length", "standard")
        language = self._store.get("ai_explanation_language", "English")
        timeout = self._store.get("ai_timeout", 180)
        use_cache = True

        if not model:
            finding.ai_status = "failed"
            finding.ai_error = "no model selected"
            self.log_line.emit(f"[ai] skip {finding.name} — no model selected")
            self.finding_updated.emit(finding)
            return

        # Check cache first (language-aware) - behavior depends on run_mode
        # NEW runs: completely bypass cache (fresh analysis)
        # RESUME/RESTORE: allow cache reuse
        # "Ask again" clears the stored answer for this one item. The write
        # below still happens, so the regenerated text replaces the entry
        # rather than leaving the old one behind it.
        force_refresh = bool(getattr(finding, "ai_force_refresh", False))
        if force_refresh:
            finding.ai_force_refresh = False
        if use_cache and not force_refresh and self._run_mode != "new":
            cached = _load_cached(finding, model, tone, length, language)
            if cached:
                finding.ai_status = "ready"
                finding.ai_explanation = cached
                finding.ai_model = model
                finding.ai_language = language
                finding.ai_updated_at = time.time()
                self._total_done += 1
                self.log_line.emit(f"[ai] cached · {finding.name}")
                self.finding_updated.emit(finding)
                return

        # Build prompt
        from datetime import datetime
        prompt = build_prompt(
            path=finding.path,
            name=finding.name,
            is_dir=finding.is_dir,
            size=finding.size,
            category=finding.category,
            risk=finding.risk,
            source_rule=finding.source_rule,
            modified=datetime.fromtimestamp(finding.modified).strftime("%Y-%m-%d"),
            accessed=datetime.fromtimestamp(finding.accessed).strftime("%Y-%m-%d"),
            tone=tone,
            length=length,
            language=language,
        )

        finding.ai_status = "analyzing"
        finding.ai_model = model
        finding.ai_language = language
        self.finding_updated.emit(finding)
        self.log_line.emit(f"[ai] analyzing {finding.name}")

        t0 = time.time()
        ok, result = generate(
            endpoint=endpoint,
            model=model,
            prompt=prompt,
            timeout=timeout,
            cancel_flag=self._cancel,
            options=_gen_options(length),
        )
        duration = time.time() - t0

        if self._cancel.is_set():
            finding.ai_status = "cancelled"
            self.finding_updated.emit(finding)
            return

        if ok:
            finding.ai_status = "ready"
            finding.ai_explanation = result
            finding.ai_language = language
            finding.ai_updated_at = time.time()
            self._total_done += 1
            self.log_line.emit(
                f"[ai] completed {finding.name} in {duration:.1f}s"
            )

            if use_cache:
                _save_cached(finding, model, tone, length, language, result)
        else:
            finding.ai_status = "failed"
            finding.ai_error = result
            self._total_failed += 1
            if "emergency timeout" in result:
                self.log_line.emit(
                    f"[ai] emergency timeout {finding.name} after {duration:.1f}s"
                )
            else:
                self.log_line.emit(
                    f"[ai] error · {finding.name} · {result} · {duration:.1f}s"
                )

        self.finding_updated.emit(finding)

    def _explain_entity(self, entity: SmartEntity):
        """Run AI explanation for one SmartEntity."""
        if self._cancel.is_set():
            return

        endpoint = self._store.get("ai_endpoint")
        model = self._store.get("ai_model", "")
        tone = self._store.get("ai_tone", "neutral")
        length = self._store.get("ai_length", "standard")
        language = self._store.get("ai_explanation_language", "English")
        timeout = self._store.get("ai_timeout", 180)
        use_cache = True

        if not model:
            entity.ai_status = "failed"
            entity.ai_error = "no model selected"
            self.log_line.emit(f"[ai] skip {entity.name} — no model selected")
            self.finding_updated.emit(entity)
            return

        # Check cache (language-aware) - behavior depends on run_mode
        # NEW runs: completely bypass cache (fresh analysis)
        # RESUME/RESTORE: allow cache reuse
        # "Ask again" clears the stored answer for this one item. The write
        # below still happens, so the regenerated text replaces the entry
        # rather than leaving the old one behind it.
        force_refresh = bool(getattr(entity, "ai_force_refresh", False))
        if force_refresh:
            entity.ai_force_refresh = False
        if use_cache and not force_refresh and self._run_mode != "new":
            cached = _load_entity_cached(entity, model, tone, length, language)
            if cached:
                entity.ai_status = "ready"
                entity.ai_explanation = cached
                entity.ai_model = model
                entity.ai_language = language
                entity.ai_updated_at = time.time()
                self._total_done += 1
                self.log_line.emit(f"[ai] cached · {entity.name}")
                self.finding_updated.emit(entity)
                return

        from app.models.smart_entity import ENTITY_TYPES
        prompt = build_entity_prompt(
            path=entity.path,
            name=entity.name,
            entity_type=entity.entity_type,
            entity_type_label=ENTITY_TYPES.get(entity.entity_type, entity.entity_type),
            size=entity.size,
            file_count=entity.file_count,
            folder_count=entity.folder_count,
            category=entity.category,
            risk=entity.risk,
            children_sample=entity.children_sample,
            parent_app=entity.parent_app,
            is_internal=entity.is_internal,
            app_version=entity.app_version,
            app_publisher=entity.app_publisher,
            tone=tone,
            length=length,
            language=language,
        )

        entity.ai_status = "analyzing"
        entity.ai_model = model
        entity.ai_language = language
        self.finding_updated.emit(entity)
        self.log_line.emit(f"[ai] analyzing {entity.name}")

        t0 = time.time()
        ok, result = generate(
            endpoint=endpoint,
            model=model,
            prompt=prompt,
            timeout=timeout,
            cancel_flag=self._cancel,
            options=_gen_options(length),
        )
        duration = time.time() - t0

        if self._cancel.is_set():
            entity.ai_status = "cancelled"
            self.finding_updated.emit(entity)
            return

        if ok:
            entity.ai_status = "ready"
            entity.ai_explanation = result
            entity.ai_language = language
            entity.ai_updated_at = time.time()
            self._total_done += 1
            self.log_line.emit(
                f"[ai] completed {entity.name} in {duration:.1f}s"
            )
            if use_cache:
                _save_entity_cached(entity, model, tone, length, language, result)
        else:
            entity.ai_status = "failed"
            entity.ai_error = result
            self._total_failed += 1
            if "emergency timeout" in result:
                self.log_line.emit(
                    f"[ai] emergency timeout {entity.name} after {duration:.1f}s"
                )
            else:
                self.log_line.emit(
                    f"[ai] error · {entity.name} · {result} · {duration:.1f}s"
                )

        self.finding_updated.emit(entity)


# ── Entity cache helpers ──────────────────────────────────────────

def _entity_cache_hash(entity, model: str, tone: str, length: str, language: str = "English") -> str:
    raw = f"{entity.cache_key}|{model}|{tone}|{length}|{language}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _load_entity_cached(entity, model: str, tone: str, length: str, language: str = "English") -> Optional[str]:
    h = _entity_cache_hash(entity, model, tone, length, language)
    p = _cache_dir() / f"ent_{h}.json"
    if p.exists():
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("explanation", None)
        except Exception:
            pass
    return None


def _save_entity_cached(entity, model: str, tone: str, length: str, language: str, explanation: str):
    h = _entity_cache_hash(entity, model, tone, length, language)
    d = _cache_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"ent_{h}.json"
        with open(p, "w", encoding="utf-8") as f:
            json.dump({
                "path": entity.path,
                "entity_type": entity.entity_type,
                "model": model,
                "tone": tone,
                "length": length,
                "language": language,
                "explanation": explanation,
                "ts": time.time(),
            }, f)
    except OSError:
        pass
