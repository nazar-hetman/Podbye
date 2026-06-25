"""Cleanup confirmation modal — shown before any deletion takes place.

Flow:
  1. User reviews summary, risk breakdown, and path list.
  2. If Review/Protected-level items are in the selection, the user must type a
     confirmation phrase before the action button activates.
  3. On confirm → CleanupWorker runs in background; dialog shows progress.
  4. On finish → result summary shown; "Close" button dismisses dialog.
"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QLineEdit, QWidget, QCheckBox,
)

from app.models.finding import _format_size
from app.models.risk import is_protected, needs_cleanup_confirmation, normalize_risk
from app.i18n import tr
from app.services.cleanup_engine import CleanupWorker
from app.services.cleanup_result_classifier import assess_cleanup_counts


# ── Confirmation phrase ───────────────────────────────────────────

def _confirm_phrase(count: int) -> str:
    return f"delete {count} items"


def _duplicate_target_sizes(item: dict) -> dict[str, int]:
    sizes: dict[str, int] = {}
    for loc in item.get("duplicate_locations") or []:
        if not isinstance(loc, dict):
            continue
        path = loc.get("path")
        if path:
            sizes[path] = int(loc.get("size_bytes", 0) or 0)
    return sizes


def _cleanup_targets_for_item(item: dict) -> list[dict]:
    """Return concrete filesystem targets for one selected finding.

    Duplicate groups are special: their display path is only the keeper /
    representative item, so cleanup must use explicit removable duplicate files.
    Older saved duplicate groups that do not carry those paths are manual-review
    only.
    """
    if is_protected(item.get("risk")):
        return []
    # Personal / mixed content containers are never whole-folder deletable —
    # the Findings UI gates this, and the dialog refuses defensively too.
    if item.get("actionability") == "review_only":
        return []
    if item.get("entity_type") != "duplicate_group":
        path = item.get("path", "")
        return [item] if path else []

    paths = [p for p in item.get("removable_duplicate_paths") or [] if p]
    if not paths:
        return []

    sizes = _duplicate_target_sizes(item)
    targets = []
    for path in paths:
        target = dict(item)
        target["path"] = path
        target["name"] = os.path.basename(path) or item.get("name", "")
        target["is_dir"] = False
        target["size_bytes"] = sizes.get(path, 0)
        target["cleanup_source_type"] = "duplicate_group"
        target["cleanup_source_path"] = item.get("path", "")
        targets.append(target)
    return targets


# ── Dialog ────────────────────────────────────────────────────────

class CleanupConfirmDialog(QDialog):
    """Modal confirmation + progress dialog for Recycle Bin cleanup.

    Args:
        items: list of finding dicts (must have 'path', 'name', 'risk', 'size_bytes').
        scan_state: optional ScanState reference for post-cleanup entity removal.
        session_id: optional session ID for cleanup-record persistence.
        log_fn: optional callable(str) for operator-feed logging.
        parent: parent widget.
    """

    def __init__(self, items: list, scan_state=None, session_id: str = "",
                 log_fn=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Confirm Cleanup"))
        self.setModal(True)
        self.setMinimumWidth(560)
        self.setMaximumWidth(760)

        self._items = items
        self._scan_state = scan_state
        self._session_id = session_id
        self._log_fn = log_fn or (lambda msg: None)
        self._worker: CleanupWorker | None = None
        self._result = None

        # Partition selected findings by canonical risk.
        self._protected = [f for f in items if normalize_risk(f.get("risk")) == "Protected"]
        self._review    = [f for f in items if normalize_risk(f.get("risk")) == "Review"]
        self._optional  = [f for f in items if normalize_risk(f.get("risk")) == "Optional"]
        self._safe      = [f for f in items if normalize_risk(f.get("risk")) == "Safe"]

        self._actionable = []
        self._manual_review = []
        for f in items:
            targets = _cleanup_targets_for_item(f)
            if targets:
                self._actionable.extend(targets)
            elif f.get("entity_type") == "duplicate_group" and not is_protected(f.get("risk")):
                self._manual_review.append(f)

        self._needs_confirmation = [
            f for f in self._actionable
            if needs_cleanup_confirmation(f.get("risk"))
        ] + self._protected

        # Cloud-synced items (subset of actionable — deletion propagates to cloud)
        self._cloud = [f for f in self._actionable if f.get("cloud_sync_provider")]
        self._total_actionable_size = sum(
            f.get("size_bytes", 0) for f in self._actionable
        )

        # Cloud acknowledgment checkbox state
        self._cloud_ack = False

        self._build_ui()

    # ── UI construction ───────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 22)
        root.setSpacing(0)

        # ── Header ───────────────────────────────────────────────
        header_lbl = QLabel(tr("Move to Recycle Bin"))
        header_lbl.setStyleSheet(
            "font-family: 'JetBrains Mono'; font-size: 15px; font-weight: bold; "
            "letter-spacing: 1px; margin-bottom: 4px;"
        )
        root.addWidget(header_lbl)

        n_action = len(self._actionable)
        sub_text = (
            f"{n_action} item(s) · {_format_size(self._total_actionable_size)} "
            "will be sent to the Recycle Bin"
        )
        sub_lbl = QLabel(sub_text)
        sub_lbl.setObjectName("Dim")
        sub_lbl.setStyleSheet("font-size: 12px; margin-bottom: 14px;")
        root.addWidget(sub_lbl)

        # ── Risk breakdown ────────────────────────────────────────
        risk_frame = QFrame()
        risk_frame.setObjectName("PanelAlt")
        risk_layout = QVBoxLayout(risk_frame)
        risk_layout.setContentsMargins(14, 10, 14, 10)
        risk_layout.setSpacing(4)

        for label, bucket, color in [
            (tr("Protected"),  self._protected, "#d68a78"),
            (tr("Review"),     self._review,    "#d8b46a"),
            (tr("Optional"),   self._optional,  "#7ab8d4"),
            (tr("Safe"),       self._safe,      "#7cc596"),
        ]:
            if not bucket:
                continue
            sz = sum(f.get("size_bytes", 0) for f in bucket)
            row = QHBoxLayout()
            dot = QLabel("●")
            dot.setStyleSheet(f"color: {color}; font-size: 10px;")
            row.addWidget(dot)
            lbl = QLabel(f"{label} — {len(bucket)} item(s) · {_format_size(sz)}")
            lbl.setStyleSheet("font-size: 12px;")
            if label == "Protected":
                lbl.setStyleSheet(
                    f"font-size: 12px; color: {color};"
                )
            row.addWidget(lbl, stretch=1)
            risk_layout.addLayout(row)

        root.addWidget(risk_frame)
        root.addSpacing(10)

        # ── Protected exclusion note ──────────────────────────────
        if self._protected:
            prot_lbl = QLabel(
                f"⚠  {len(self._protected)} protected item(s) will be skipped — "
                "system-critical paths are never deleted."
            )
            prot_lbl.setStyleSheet(
                "font-size: 11px; color: #d68a78; padding: 6px 0;"
            )
            prot_lbl.setWordWrap(True)
            root.addWidget(prot_lbl)
            root.addSpacing(6)

        if self._manual_review:
            manual_lbl = QLabel(
                f"{len(self._manual_review)} duplicate group(s) need manual review. "
                "No cleanup target was queued because explicit removable duplicate files were not captured."
            )
            manual_lbl.setStyleSheet(
                "font-size: 11px; color: #d8b46a; padding: 6px 0;"
            )
            manual_lbl.setWordWrap(True)
            root.addWidget(manual_lbl)
            root.addSpacing(6)

        # ── Cloud-sync warning (requires explicit acknowledgment) ────
        self._cloud_cb: QCheckBox | None = None
        if self._cloud:
            providers = sorted({f.get("cloud_sync_provider", "Cloud") for f in self._cloud})
            provider_str = " / ".join(providers)
            cloud_frame = QFrame()
            cloud_frame.setObjectName("PanelAlt")
            cloud_layout = QVBoxLayout(cloud_frame)
            cloud_layout.setContentsMargins(14, 10, 14, 10)
            cloud_layout.setSpacing(6)

            cloud_hdr = QLabel(f"☁  Cloud-synced items ({provider_str})")
            cloud_hdr.setStyleSheet("font-size: 12px; font-weight: bold; color: #7ab8d4;")
            cloud_layout.addWidget(cloud_hdr)

            cloud_warn = QLabel(
                f"{len(self._cloud)} item(s) are inside a cloud-sync folder. "
                "Deletion will propagate to your cloud account and all synced devices."
            )
            cloud_warn.setStyleSheet("font-size: 11px; color: #b0c8d4;")
            cloud_warn.setWordWrap(True)
            cloud_layout.addWidget(cloud_warn)

            self._cloud_cb = QCheckBox(
                tr("I understand this will delete files from my cloud account")
            )
            self._cloud_cb.setStyleSheet("font-size: 11px;")
            self._cloud_cb.toggled.connect(self._update_confirm_btn)
            cloud_layout.addWidget(self._cloud_cb)

            root.addWidget(cloud_frame)
            root.addSpacing(10)

        # ── Scrollable path list (non-routine decisions, max 50 shown) ─
        show_paths = self._needs_confirmation + [
            f for f in self._actionable
            if normalize_risk(f.get("risk")) == "Optional"
        ]
        if show_paths:
            list_header = QLabel(
                tr("Items worth checking ({count}):").format(count=len(show_paths))
            )
            list_header.setObjectName("Dim")
            list_header.setStyleSheet("font-size: 11px; margin-bottom: 4px;")
            root.addWidget(list_header)

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setMaximumHeight(160)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll.setStyleSheet("QScrollArea { border: none; }")

            container = QWidget()
            c_layout = QVBoxLayout(container)
            c_layout.setContentsMargins(0, 0, 0, 0)
            c_layout.setSpacing(2)

            for f in show_paths[:50]:
                row_lbl = QLabel(
                    f"  {f.get('risk', '?')}  {f.get('name', os.path.basename(f.get('path', '')))}"
                    f"  ·  {_format_size(f.get('size_bytes', 0))}"
                )
                row_lbl.setStyleSheet(
                    "font-family: 'JetBrains Mono'; font-size: 10px;"
                )
                c_layout.addWidget(row_lbl)

            if len(show_paths) > 50:
                more_lbl = QLabel(f"  … and {len(show_paths) - 50} more")
                more_lbl.setObjectName("Dim")
                more_lbl.setStyleSheet("font-size: 10px;")
                c_layout.addWidget(more_lbl)

            c_layout.addStretch()
            scroll.setWidget(container)
            root.addWidget(scroll)
            root.addSpacing(10)

        # ── Confirmation phrase (Review/Protected/Risk items) ─────
        self._confirm_input: QLineEdit | None = None
        self._confirm_hint: QLabel | None = None
        if self._needs_confirmation:
            phrase = _confirm_phrase(len(self._actionable))
            hint = QLabel(tr("Type  <b>{phrase}</b>  to confirm:").format(phrase=phrase))
            hint.setStyleSheet("font-size: 12px; margin-bottom: 4px;")
            hint.setTextFormat(Qt.RichText)
            root.addWidget(hint)
            self._confirm_hint = hint

            self._confirm_input = QLineEdit()
            self._confirm_input.setPlaceholderText(phrase)
            self._confirm_input.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 12px;")
            self._confirm_input.textChanged.connect(self._on_phrase_changed)
            root.addWidget(self._confirm_input)
            root.addSpacing(10)

        # ── Progress area (hidden until worker starts) ────────────
        self._progress_frame = QFrame()
        self._progress_frame.setVisible(False)
        prog_layout = QVBoxLayout(self._progress_frame)
        prog_layout.setContentsMargins(0, 0, 0, 0)
        prog_layout.setSpacing(4)

        self._progress_lbl = QLabel(tr("Preparing…"))
        self._progress_lbl.setObjectName("Dim")
        self._progress_lbl.setStyleSheet(
            "font-family: 'JetBrains Mono'; font-size: 11px;"
        )
        prog_layout.addWidget(self._progress_lbl)

        self._result_lbl = QLabel("")
        self._result_lbl.setStyleSheet("font-size: 12px; font-weight: bold;")
        self._result_lbl.setWordWrap(True)
        self._result_lbl.setVisible(False)
        prog_layout.addWidget(self._result_lbl)

        root.addWidget(self._progress_frame)

        # ── Button row ────────────────────────────────────────────
        root.addSpacing(10)
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self._btn_cancel = QPushButton(tr("Cancel"))
        self._btn_cancel.setObjectName("Subtle")
        self._btn_cancel.setFixedWidth(90)
        self._btn_cancel.clicked.connect(self._on_cancel)
        btn_row.addWidget(self._btn_cancel)

        self._btn_confirm = QPushButton(tr("Move to Recycle Bin"))
        self._btn_confirm.setObjectName("Primary")
        self._btn_confirm.setFixedWidth(180)
        self._btn_confirm.clicked.connect(self._on_confirm)
        btn_row.addWidget(self._btn_confirm)

        root.addLayout(btn_row)

        # Set initial confirm button state
        self._update_confirm_btn()

    # ── Event handlers ────────────────────────────────────────────

    def _update_confirm_btn(self):
        """Re-evaluate whether the confirm button should be enabled."""
        if not self._actionable:
            self._btn_confirm.setEnabled(False)
            return
        # Phrase required when Review/Protected/Risk-equivalent items are present.
        if self._needs_confirmation:
            phrase = _confirm_phrase(len(self._actionable))
            phrase_ok = bool(self._confirm_input and
                             self._confirm_input.text().strip() == phrase)
        else:
            phrase_ok = True
        # Cloud ack required when cloud items present
        cloud_ok = (not self._cloud) or bool(self._cloud_cb and self._cloud_cb.isChecked())
        self._btn_confirm.setEnabled(phrase_ok and cloud_ok)

    def _on_phrase_changed(self, text: str):
        self._update_confirm_btn()

    def _on_cancel(self):
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._btn_cancel.setEnabled(False)
            self._progress_lbl.setText(tr("Cancelling…"))
            return
        self.reject()

    def _on_confirm(self):
        """Start the cleanup worker."""
        self._btn_confirm.setEnabled(False)
        self._btn_confirm.setText(tr("Moving…"))
        if self._confirm_input:
            self._confirm_input.setEnabled(False)

        # Show progress area
        self._progress_frame.setVisible(True)
        self._progress_lbl.setText(f"Moving 0 / {len(self._actionable)}…")

        paths = [f["path"] for f in self._actionable]

        self._worker = CleanupWorker(
            paths=paths,
            mode=CleanupWorker.MODE_RECYCLE,
            parent=self,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.log_line.connect(self._log_fn)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _on_progress(self, done: int, total: int, path: str):
        if path:
            name = os.path.basename(path) or path
            self._progress_lbl.setText(
                f"Moving {done + 1} / {total} — {name}"
            )
        else:
            self._progress_lbl.setText(tr("Done — {total} item(s) processed").format(total=total))

    def _on_finished(self, result):
        self._result = result
        n_ok   = len(result.succeeded)
        n_in_use = len(result.in_use)
        n_fail = len(result.failed)
        n_skip = len(result.skipped_protected)
        assessment = assess_cleanup_counts(
            succeeded_count=n_ok,
            in_use_count=n_in_use,
            failed_count=n_fail,
            skipped_count=n_skip,
            category_label="Selected items",
        )

        # Build result text
        freed = _format_size(result.total_bytes_freed)
        if n_ok:
            ok_msg = f"✓  {n_ok} item(s) moved to Recycle Bin · {freed} freed"
        else:
            ok_msg = tr("No items were moved")

        parts = [ok_msg]
        if n_in_use:
            parts.append(f"•  {n_in_use} file(s) currently in use")
        if n_fail:
            parts.append(f"•  {n_fail} unexpected issue(s) need attention")
        if n_skip:
            parts.append(f"•  {n_skip} protected item(s) skipped")
        parts.append("")
        parts.append(assessment.explanation_text)

        self._result_lbl.setText("\n".join(parts))
        self._result_lbl.setVisible(True)
        self._progress_lbl.setVisible(False)

        # Post-cleanup: remove entities from scan state
        if self._scan_state and result.succeeded:
            try:
                succeeded = set(result.succeeded)
                remove_paths = set(succeeded)
                duplicate_sources: dict[str, set[str]] = {}
                for f in self._actionable:
                    if f.get("cleanup_source_type") != "duplicate_group":
                        continue
                    source = f.get("cleanup_source_path", "")
                    if source:
                        duplicate_sources.setdefault(source, set()).add(f.get("path", ""))
                for source, targets in duplicate_sources.items():
                    if targets and targets.issubset(succeeded):
                        remove_paths.add(source)
                self._scan_state.remove_entities_by_path(remove_paths)
            except Exception:
                pass

        # Write cleanup history record
        if result.succeeded or result.in_use or result.failed:
            try:
                from app.state.session_store import save_cleanup_record
                save_cleanup_record(
                    session_id=self._session_id,
                    items=[
                        {
                            "path": f["path"],
                            "name": f.get("name", ""),
                            "size": f.get("size_bytes", 0),
                            "risk": f.get("risk", ""),
                            "category": f.get("category", ""),
                        }
                        for f in self._actionable
                    ],
                    result=result,
                    mode=CleanupWorker.MODE_RECYCLE,
                )
            except Exception:
                pass

        # Switch buttons: hide Cancel, change Confirm to Close
        self._btn_cancel.setVisible(False)
        self._btn_confirm.setEnabled(True)
        self._btn_confirm.setText(tr("Close"))
        self._btn_confirm.clicked.disconnect()
        self._btn_confirm.clicked.connect(self.accept)

    # ── Prevent accidental close during operation ─────────────────

    def closeEvent(self, event):
        if self._worker and self._worker.isRunning():
            event.ignore()
        else:
            super().closeEvent(event)

    def keyPressEvent(self, event):
        if (event.key() in (Qt.Key_Escape, Qt.Key_Return, Qt.Key_Enter)
                and self._worker and self._worker.isRunning()):
            event.ignore()
            return
        super().keyPressEvent(event)

    # ── Result accessor ───────────────────────────────────────────

    def cleanup_result(self):
        """Return the CleanupResult, or None if not yet completed."""
        return self._result
