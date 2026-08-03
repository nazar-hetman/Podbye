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
import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QWidget, QCheckBox, QProgressBar,
)

from app.models.finding import _format_size
from app.models.risk import (
    is_protected, normalize_risk, risk_fg as _risk_fg,
)
from app.i18n import tr
from app.themes.theme_manager import get_palette
from app.services.cleanup_engine import CleanupWorker
from app.services.cleanup_result_classifier import assess_cleanup_counts


def _elide_middle(text: str, limit: int) -> str:
    """Shorten *text* from the middle, keeping both ends readable.

    File names collide at the front (setup-1.exe, setup-2.exe) and carry their
    extension at the back, so trimming either end alone loses the part that
    tells two items apart.
    """
    if limit <= 1 or len(text) <= limit:
        return text
    keep = limit - 1
    head = (keep + 1) // 2
    return text[:head] + "…" + text[len(text) - (keep - head):]


def _is_drive_root_path(path: str) -> bool:
    """True for a bare drive root such as 'C:/', 'C:\\', or 'C:'."""
    stripped = path.replace("\\", "/").rstrip("/")
    return bool(re.fullmatch(r"[A-Za-z]:", stripped))


def _is_review_tier(target: dict) -> bool:
    """True for items that need an explicit opt-in before deletion.

    Review-risk items and 'uncertain' content (Unknown / mixed / personal
    folders, flagged review_only) require the acknowledgment checkbox; plain
    Safe / Optional items do not.
    """
    if normalize_risk(target.get("risk")) == "Review":
        return True
    return target.get("actionability") == "review_only"


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
    # Note: review_only items (personal/mixed/unknown) ARE returned here — the
    # dialog gates them behind an explicit "delete review items" acknowledgment
    # rather than refusing them outright. Only Protected is never deletable.

    # Grouped / loose buckets carry the actual files they stand for. Expand to
    # per-file targets so cleanup never recycles the bucket's display path
    # (which can be the scan root or a drive root).
    file_paths = [p for p in (item.get("removable_file_paths") or []) if p]
    if file_paths and item.get("entity_type") != "duplicate_group":
        targets = []
        for path in file_paths:
            target = dict(item)
            target["path"] = path
            target["name"] = os.path.basename(path) or item.get("name", "")
            target["is_dir"] = False
            target["removable_file_paths"] = []
            targets.append(target)
        return targets

    if item.get("entity_type") != "duplicate_group":
        path = item.get("path", "")
        # Safety net: never offer a drive root (C:/) or empty path as a target.
        if not path or _is_drive_root_path(path):
            return []
        return [item]

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
                 log_fn=None, auto_confirm: bool = False, parent=None):
        super().__init__(parent)
        self._auto_confirm = auto_confirm
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

        # Expand selected findings into concrete file/folder targets, then split
        # them into "safe to remove now" vs "review/uncertain" (needs an opt-in).
        all_targets = []
        self._manual_review = []
        for f in items:
            targets = _cleanup_targets_for_item(f)
            if targets:
                all_targets.extend(targets)
            elif f.get("entity_type") == "duplicate_group" and not is_protected(f.get("risk")):
                self._manual_review.append(f)

        self._safe_targets = [t for t in all_targets if not _is_review_tier(t)]
        self._review_targets = [t for t in all_targets if _is_review_tier(t)]

        # Review items are armed by default — the user explicitly selected them,
        # so there is no opt-in tick. A confirmation dialog is still shown (this
        # dialog), unless the user has turned that off via "Don't ask again".
        self._review_ack = True

        # Cloud-synced items (subset of all targets — deletion propagates to cloud)
        self._cloud = [t for t in all_targets if t.get("cloud_sync_provider")]
        self._cloud_ack = False

        self._build_ui()

    # ── Armed target set ──────────────────────────────────────────

    def _armed_targets(self) -> list:
        """Files that will actually be removed given the current acknowledgments."""
        targets = list(self._safe_targets)
        if self._review_ack:
            targets += self._review_targets
        return targets

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

        self._sub_lbl = QLabel("")
        self._sub_lbl.setObjectName("Dim")
        self._sub_lbl.setStyleSheet("font-size: 12px; margin-bottom: 14px;")
        self._sub_lbl.setWordWrap(True)
        root.addWidget(self._sub_lbl)

        # ── Risk breakdown ────────────────────────────────────────
        risk_frame = QFrame()
        risk_frame.setObjectName("PanelAlt")
        risk_layout = QVBoxLayout(risk_frame)
        risk_layout.setContentsMargins(14, 10, 14, 10)
        risk_layout.setSpacing(4)

        for label, bucket, color in [
            (tr("Protected"),  self._protected, _risk_fg("Protected")),
            (tr("Review"),     self._review,    _risk_fg("Review")),
            (tr("Optional"),   self._optional,  _risk_fg("Optional")),
            (tr("Safe"),       self._safe,      _risk_fg("Safe")),
        ]:
            if not bucket:
                continue
            sz = sum(f.get("size_bytes", 0) for f in bucket)
            row = QHBoxLayout()
            dot = QLabel("●")
            dot.setStyleSheet(f"color: {color}; font-size: 10px;")
            row.addWidget(dot)
            lbl = QLabel(tr("{label} — {n} item(s) · {size}",
                            label=label, n=len(bucket), size=_format_size(sz)))
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
            prot_lbl = QLabel(tr(
                "⚠  {n} protected item(s) will be skipped — system-critical "
                "paths are never deleted.", n=len(self._protected)))
            prot_lbl.setStyleSheet(
                f"font-size: 11px; color: {_risk_fg('Protected')}; padding: 6px 0;"
            )
            prot_lbl.setWordWrap(True)
            root.addWidget(prot_lbl)
            root.addSpacing(6)

        if self._manual_review:
            manual_lbl = QLabel(tr(
                "{n} duplicate group(s) need manual review. No cleanup target "
                "was queued because explicit removable duplicate files were not "
                "captured.", n=len(self._manual_review)))
            manual_lbl.setStyleSheet(
                f"font-size: 11px; color: {_risk_fg('Review')}; padding: 6px 0;"
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

            _pal = get_palette()
            _cloud_color = _pal.get("optional", "#6e93a8")
            cloud_hdr = QLabel(tr("☁  Cloud-synced items ({provider})",
                                  provider=provider_str))
            cloud_hdr.setStyleSheet(
                f"font-size: 12px; font-weight: bold; color: {_cloud_color};")
            cloud_layout.addWidget(cloud_hdr)

            cloud_warn = QLabel(tr(
                "{n} item(s) are inside a cloud-sync folder. Deletion will "
                "propagate to your cloud account and all synced devices.",
                n=len(self._cloud)))
            cloud_warn.setStyleSheet(f"font-size: 11px; color: {_pal.get('text_dim', '#8a9b8f')};")
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

        # ── Scrollable preview of the review/uncertain items ──────────
        if self._review_targets:
            list_header = QLabel(
                tr("Review / uncertain items ({count}):").format(
                    count=len(self._review_targets))
            )
            list_header.setObjectName("Dim")
            list_header.setStyleSheet("font-size: 11px; margin-bottom: 4px;")
            root.addWidget(list_header)

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setMaximumHeight(140)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll.setStyleSheet("QScrollArea { border: none; }")

            container = QWidget()
            c_layout = QVBoxLayout(container)
            c_layout.setContentsMargins(0, 0, 0, 0)
            c_layout.setSpacing(2)

            for f in self._review_targets[:50]:
                row_lbl = QLabel(
                    f"  {normalize_risk(f.get('risk'))}  "
                    f"{f.get('name', os.path.basename(f.get('path', '')))}"
                    f"  ·  {_format_size(f.get('size_bytes', 0))}"
                )
                row_lbl.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 10px;")
                c_layout.addWidget(row_lbl)

            if len(self._review_targets) > 50:
                more_lbl = QLabel(tr("  … and {n} more",
                                     n=len(self._review_targets) - 50))
                more_lbl.setObjectName("Dim")
                more_lbl.setStyleSheet("font-size: 10px;")
                c_layout.addWidget(more_lbl)

            c_layout.addStretch()
            scroll.setWidget(container)
            root.addWidget(scroll)
            root.addSpacing(8)

        # ── "Don't ask again" (only when review/uncertain items are present) ─
        # Review items are already armed; this simply lets the user skip this
        # confirmation for future review cleanups. It is stored as the existing
        # confirm_risky_cleanup setting so it stays reversible in Settings.
        self._dont_ask_cb: QCheckBox | None = None
        if self._review_targets:
            self._dont_ask_cb = QCheckBox(
                tr("Don't ask again for review/uncertain items")
            )
            self._dont_ask_cb.setStyleSheet("font-size: 12px;")
            root.addWidget(self._dont_ask_cb)
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

        # A moving bar, because the item counter alone does not move. Recycling
        # one large folder is a single item, so the counter sat at "1 / 1" for
        # the whole operation while the dialog refused to close — indis-
        # tinguishable from a hang, which is exactly how it was reported.
        self._progress_bar = QProgressBar()
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setFixedHeight(6)
        prog_layout.addWidget(self._progress_bar)

        # The item currently being moved. With one entry per line the label
        # above can stay a stable "Moving 3 of 12", and this shows the movement.
        self._progress_path_lbl = QLabel("")
        self._progress_path_lbl.setObjectName("Dim")
        self._progress_path_lbl.setStyleSheet(
            "font-family: 'JetBrains Mono'; font-size: 10px;"
        )
        self._progress_path_lbl.setWordWrap(False)
        prog_layout.addWidget(self._progress_path_lbl)

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

        # Set initial sub-label + confirm button state
        self._update_sub_label()
        self._update_confirm_btn()

        # When the user has turned confirmation off, skip straight to the move
        # (still showing progress/result in this dialog) instead of asking.
        if self._auto_confirm and self._armed_targets():
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, self._on_confirm)

    # ── Event handlers ────────────────────────────────────────────

    def _update_sub_label(self):
        """Describe what the confirm button will actually remove right now."""
        armed = self._armed_targets()
        size = sum(t.get("size_bytes", 0) for t in armed)
        if not armed:
            self._sub_lbl.setText(tr("Nothing to remove."))
            return
        text = tr("{n} item(s) · {size} will be sent to the Recycle Bin").format(
            n=len(armed), size=_format_size(size))
        if self._review_targets:
            text += tr("  ·  includes {n} review/uncertain item(s)").format(
                n=len(self._review_targets))
        self._sub_lbl.setText(text)

    def _update_confirm_btn(self):
        """Enable confirm only when something is armed and cloud ack (if any) is set."""
        armed = self._armed_targets()
        if not armed:
            self._btn_confirm.setEnabled(False)
            return
        cloud_ok = (not self._cloud) or bool(self._cloud_cb and self._cloud_cb.isChecked())
        self._btn_confirm.setEnabled(cloud_ok)

    def _on_cancel(self):
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._btn_cancel.setEnabled(False)
            # Cancellation takes effect between items, so the item in flight
            # still has to finish — keep the bar sweeping until it does.
            self._progress_bar.setRange(0, 0)
            self._progress_lbl.setText(tr("Cancelling…"))
            return
        self.reject()

    def _on_confirm(self):
        """Start the cleanup worker on the currently-armed target set."""
        self._armed = self._armed_targets()
        if not self._armed:
            return
        # Persist "Don't ask again" before starting — reversible in Settings.
        if self._dont_ask_cb and self._dont_ask_cb.isChecked():
            store = getattr(self._scan_state, "_settings_store", None)
            if store is not None:
                try:
                    store.set_and_save("confirm_risky_cleanup", False)
                except Exception:
                    pass
        self._btn_confirm.setEnabled(False)
        self._btn_confirm.setText(tr("Moving…"))
        if self._dont_ask_cb:
            self._dont_ask_cb.setEnabled(False)
        if self._cloud_cb:
            self._cloud_cb.setEnabled(False)

        # Show progress area. A single item gets an indeterminate bar: there is
        # no meaningful fraction to show, and one big folder is precisely the
        # case where a static "1 / 1" looked frozen.
        total = len(self._armed)
        self._progress_frame.setVisible(True)
        self._progress_bar.setRange(0, 0 if total <= 1 else total)
        self._progress_bar.setValue(0)
        self._progress_path_lbl.setText("")
        self._progress_lbl.setText(
            tr("Moving to the Recycle Bin…") if total <= 1
            else tr("Moving 0 / {total}…", total=total))

        paths = [f["path"] for f in self._armed]

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
        """Worker progress: *done* items finished, *path* is the one starting now.

        The count used to be reported as ``done + 1``, which announced an item
        as moved before the move was attempted — so the last line the user saw
        on a failure named the item that had actually succeeded.
        """
        if path:
            if total > 1:
                self._progress_bar.setValue(done)
                self._progress_lbl.setText(tr(
                    "Moving {done} / {total}…", done=done, total=total))
            self._progress_path_lbl.setText(
                _elide_middle(os.path.basename(path) or path, 52))
        else:
            if total > 1:
                self._progress_bar.setRange(0, total)
                self._progress_bar.setValue(total)
            else:
                self._progress_bar.setRange(0, 1)
                self._progress_bar.setValue(1)
            self._progress_path_lbl.setText("")
            self._progress_lbl.setText(
                tr("Done — {total} item(s) processed", total=total))

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
            retry_label="the cleanup",
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
        # The bar is finished with too — leaving an indeterminate one sweeping
        # under the result text reads as "still working".
        self._progress_bar.setVisible(False)
        self._progress_path_lbl.setVisible(False)

        armed = getattr(self, "_armed", self._armed_targets())

        # Post-cleanup: remove entities from scan state
        if self._scan_state and result.succeeded:
            try:
                succeeded = set(result.succeeded)
                remove_paths = set(succeeded)
                duplicate_sources: dict[str, set[str]] = {}
                for f in armed:
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
                        for f in armed
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
