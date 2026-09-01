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

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QWidget, QCheckBox, QProgressBar,
    QSpacerItem, QSizePolicy,
)

from app.widgets.controls import ElidedLabel, TacticalCheckBox
from app.models.finding import _format_size
from app.models.risk import (
    is_protected, normalize_risk, risk_fg as _risk_fg, risk_sort_index,
)
from app.i18n import tr, tr_count
from app.themes.theme_manager import get_palette
from app.services.cleanup_engine import CleanupWorker
from app.services.keep_list import is_kept
from app.services.cleanup_result_classifier import assess_cleanup_counts


# How many target paths the plan lists before it says "and N more". The list
# scrolls inside a fixed box, so this is about the cost of building rows, not
# about how much the user is allowed to see.
_PREVIEW_ROWS = 200


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
    stripped = path.replace("\\", "/").strip().rstrip("/")
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


def _file_size(path: str) -> int:
    """Bytes on disk, or 0 when the file cannot be measured.

    0 is the honest answer for a path that has since gone: it contributes
    nothing to the "will be sent" total, which is what removing it will free.
    """
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


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
    # Kept by the user. Checked here as well as in the engine because this is
    # where the *plan* is built: a kept item must never be counted, sized or
    # listed as something about to be deleted.
    if is_kept(item.get("path", "")):
        return []
    # Note: review_only items (personal/mixed/unknown) ARE returned here — the
    # dialog gates them behind an explicit "delete review items" acknowledgment
    # rather than refusing them outright. Only Protected is never deletable.

    # Grouped / loose buckets carry the actual files they stand for. Expand to
    # per-file targets so cleanup never recycles the bucket's display path
    # (which can be the scan root or a drive root).
    # Same normalisation the Files tab applies before showing this list: a
    # whitespace-only entry is not a file, and a repeat would be attempted —
    # and reported — twice.
    # The drive-root screen below applies to a folder-backed entity. It has to
    # apply here too: this branch produced a target of "C:/" from a file list
    # containing one, and "never offer a drive root as a target" is not a rule
    # that can hold on one of two paths through the same function.
    file_paths = list(dict.fromkeys(
        p for p in (item.get("removable_file_paths") or []) if p and p.strip()
        and not _is_drive_root_path(p)))
    if file_paths and item.get("entity_type") != "duplicate_group":
        targets = []
        for path in file_paths:
            target = dict(item)
            target["path"] = path
            target["name"] = os.path.basename(path) or item.get("name", "")
            target["is_dir"] = False
            target["removable_file_paths"] = []
            # Each file weighs what it weighs. dict(item) copies the *bucket's*
            # size onto every one of its members, so a nine-file selection out
            # of a 240 MB folder was announced as "9 item(s) · 2.1 GB will be
            # sent to the Recycle Bin" — the entity's size, nine times over, on
            # the last screen before anything is deleted. The duplicate branch
            # below already looked its sizes up per path; this one did not.
            target["size_bytes"] = _file_size(path)
            target["reclaimable_bytes"] = target["size_bytes"]
            targets.append(target)
        return targets

    if item.get("entity_type") != "duplicate_group":
        path = item.get("path", "")
        # Safety net: never offer a drive root (C:/) or empty path as a target.
        if not path or _is_drive_root_path(path):
            return []

        # A folder that holds a separately listed finding does not own it. The
        # whole folder used to be handed to the shell, which took that finding
        # with it — bytes charged to another row, removed without being
        # counted anywhere the user could see. Expanding here rather than in
        # the engine keeps the engine a plain list of paths to delete, which
        # is the property that makes it auditable.
        from app.models.deletion_scope import excluded_paths, expand_targets

        keep = excluded_paths(item)
        if not keep:
            return [item]

        # One target, carrying the keep-out list. The expansion itself is a
        # directory walk and this function runs in CleanupConfirmDialog's
        # constructor, on the UI thread, from the click that opens it —
        # measured at 2.8 s for a 23,052-file tree (C:/Windows/System32) and
        # 0.6 s for 11,150 files. Expanding here froze the app between the
        # click and the dialog appearing, once per selected item.
        #
        # Nothing is lost by deferring it. The size is already known exactly:
        # own bytes are the folder minus the nested findings, which is
        # precisely what the expansion adds up to, so no walk is needed to
        # state the total. The paths are produced in the cleanup worker
        # thread, just before they are needed.
        target = dict(item)
        target["cleanup_exclude_paths"] = list(keep)
        return [target]

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
        self._kept = [f for f in items if is_kept(f.get("path", ""))]
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

        # Everything here that describes what *will* happen. Once it has
        # happened these stop being true, so they are collected on the way in
        # and taken down in one go when the result arrives — see
        # _present_as_result(). Before that fix the finished dialog stacked
        # "203 items · 13.1 GB will be sent" directly above "135 moved · 42
        # issues": the plan and the outcome, contradicting each other, in one
        # scroll.
        self._confirm_only: list = []
        self._confirm_spacers: list = []

        def _confirm_gap(px: int):
            """A gap that belongs to the confirmation, and goes down with it.

            addSpacing() plants a fixed spacer that outlives the widget it was
            meant to separate, so hiding the risk panel, the review list and
            the "don't ask again" tick still left ~30px of nothing between the
            result headline and the issue list.
            """
            spacer = QSpacerItem(0, px, QSizePolicy.Minimum, QSizePolicy.Fixed)
            root.addItem(spacer)
            self._confirm_spacers.append(spacer)

        # ── Header ───────────────────────────────────────────────
        self._header_lbl = QLabel(tr("Move to Recycle Bin"))
        self._header_lbl.setStyleSheet(
            "font-family: 'JetBrains Mono'; font-size: 15px; font-weight: bold; "
            "letter-spacing: 1px; margin-bottom: 4px;"
        )
        root.addWidget(self._header_lbl)

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
            lbl = QLabel(tr_count("{label} — {n} item(s) · {size}", len(bucket),
                                  label=label, n=len(bucket), size=_format_size(sz)))
            lbl.setStyleSheet("font-size: 12px;")
            if label == "Protected":
                lbl.setStyleSheet(
                    f"font-size: 12px; color: {color};"
                )
            row.addWidget(lbl, stretch=1)
            risk_layout.addLayout(row)

        root.addWidget(risk_frame)
        self._confirm_only.append(risk_frame)
        _confirm_gap(10)

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
            self._confirm_only.append(prot_lbl)
            _confirm_gap(6)

        if self._kept:
            kept_lbl = QLabel(tr(
                "{n} item(s) you are keeping were left out of this cleanup.",
                n=len(self._kept)))
            kept_lbl.setStyleSheet(
                f"font-size: 11px; color: {_risk_fg('Optional')}; padding: 6px 0;")
            kept_lbl.setWordWrap(True)
            root.addWidget(kept_lbl)
            self._confirm_only.append(kept_lbl)
            _confirm_gap(6)

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
            self._confirm_only.append(manual_lbl)
            _confirm_gap(6)

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
            cloud_hdr = QLabel(tr("⇧  Cloud-synced items ({provider})",
                                  provider=provider_str))
            cloud_hdr.setStyleSheet(
                f"font-size: 12px; font-weight: bold; color: {_cloud_color};")
            cloud_layout.addWidget(cloud_hdr)

            cloud_warn = QLabel(tr_count(
                "{n} item(s) are inside a cloud-sync folder. Deletion will "
                "propagate to your cloud account and all synced devices.",
                len(self._cloud), n=len(self._cloud)))
            cloud_warn.setStyleSheet(f"font-size: 11px; color: {_pal.get('text_dim', '#8a9b8f')};")
            cloud_warn.setWordWrap(True)
            cloud_layout.addWidget(cloud_warn)

            self._cloud_cb = TacticalCheckBox(
                tr("I understand this will delete files from my cloud account")
            )
            self._cloud_cb.setStyleSheet("font-size: 11px;")
            self._cloud_cb.toggled.connect(self._update_confirm_btn)
            cloud_layout.addWidget(self._cloud_cb)

            root.addWidget(cloud_frame)
            self._confirm_only.append(cloud_frame)
            _confirm_gap(10)

        # ── Scrollable preview of every armed target ─────────────────
        # It used to list the review-tier targets only, so a selection of 384
        # items showed four counts and a partial list. Reported as: it is
        # unclear what exactly he is deleting. Every path that will be touched
        # is here, riskiest first.
        preview = sorted(
            self._armed_targets(),
            key=lambda t: (-risk_sort_index(normalize_risk(t.get("risk"))),
                           -int(t.get("size_bytes", 0) or 0)))
        if preview:
            list_header = QLabel(
                tr("Everything this will move ({count}):").format(
                    count=len(preview))
            )
            list_header.setObjectName("Dim")
            list_header.setStyleSheet("font-size: 11px; margin-bottom: 4px;")
            root.addWidget(list_header)
            self._confirm_only.append(list_header)

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll.setStyleSheet("QScrollArea { border: none; }")

            container = QWidget()
            c_layout = QVBoxLayout(container)
            c_layout.setContentsMargins(0, 0, 0, 0)
            c_layout.setSpacing(2)

            for f in preview[:_PREVIEW_ROWS]:
                path = f.get("path", "")
                # The PATH, not just the name. This dialog is the last thing
                # between the user and a deletion, and "Adobe Photoshop 2024"
                # does not say which copy or where it lives. Elided in the
                # middle so the drive and the leaf both stay visible, with the
                # whole thing in the tooltip.
                row_line = (
                    f"{tr(normalize_risk(f.get('risk')))}   {path}"
                    f"   ·   {_format_size(f.get('size_bytes', 0))}"
                )
                row_lbl = ElidedLabel(row_line, mode=Qt.ElideMiddle)
                # Stated explicitly, so it is there whether or not the line was
                # cut. Elsewhere a label offers its text on hover only when it
                # is too narrow to show it — repeating something already
                # readable is noise. This is the list you confirm a deletion
                # from, and being able to check the exact path of every row the
                # same way, without judging first whether it looks truncated,
                # is worth the redundancy.
                row_lbl.setToolTip(row_line)
                row_lbl.setStyleSheet("font-family: 'JetBrains Mono'; font-size: 10px;")
                c_layout.addWidget(row_lbl)

            if len(preview) > _PREVIEW_ROWS:
                more_lbl = QLabel(tr("  … and {n} more",
                                     n=len(preview) - _PREVIEW_ROWS))
                more_lbl.setObjectName("Dim")
                more_lbl.setStyleSheet("font-size: 10px;")
                c_layout.addWidget(more_lbl)

            c_layout.addStretch()
            scroll.setWidget(container)
            # Fit the content instead of always reserving the cap: a single
            # review item was given a 79px box to sit 13px of text in, leaving
            # a hole in the middle of the dialog.
            scroll.setMaximumHeight(140)
            scroll.setFixedHeight(min(140, container.sizeHint().height() + 4))
            root.addWidget(scroll)
            self._confirm_only.append(scroll)
            _confirm_gap(8)

        # ── "Don't ask again" (only when review/uncertain items are present) ─
        # Review items are already armed; this simply lets the user skip this
        # confirmation for future review cleanups. It is stored as the existing
        # confirm_risky_cleanup setting so it stays reversible in Settings.
        self._dont_ask_cb: QCheckBox | None = None
        if self._review_targets:
            self._dont_ask_cb = TacticalCheckBox(
                tr("Don't ask again for review/uncertain items")
            )
            self._dont_ask_cb.setStyleSheet("font-size: 12px;")
            root.addWidget(self._dont_ask_cb)
            _confirm_gap(10)

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

        root.addWidget(self._progress_frame)

        # ── Issue list (hidden until there is something in it) ────
        # "42 unexpected issue(s) need attention" is not something a user can
        # act on. Which 42, and why, is. The paths live behind a chevron so a
        # clean run stays a two-line dialog, and Copy list exists because the
        # next step for a locked file usually happens outside Podbye.
        self._issues_frame = QFrame()
        self._issues_frame.setVisible(False)
        iss_layout = QVBoxLayout(self._issues_frame)
        iss_layout.setContentsMargins(0, 8, 0, 0)
        iss_layout.setSpacing(4)

        iss_head = QHBoxLayout()
        iss_head.setSpacing(8)
        self._btn_issues = QPushButton("")
        self._btn_issues.setObjectName("Subtle")
        self._btn_issues.setCursor(Qt.PointingHandCursor)
        self._btn_issues.setStyleSheet(
            "QPushButton { background: transparent; border: none; "
            "text-align: left; font-size: 11px; padding: 0; }")
        self._btn_issues.clicked.connect(self._toggle_issues)
        iss_head.addWidget(self._btn_issues, stretch=1)
        self._btn_copy_issues = QPushButton(tr("Copy list"))
        self._btn_copy_issues.setObjectName("Subtle")
        self._btn_copy_issues.setCursor(Qt.PointingHandCursor)
        self._btn_copy_issues.setStyleSheet("font-size: 10px; padding: 2px 10px;")
        self._btn_copy_issues.clicked.connect(self._copy_issue_list)
        iss_head.addWidget(self._btn_copy_issues)
        iss_layout.addLayout(iss_head)

        self._issues_scroll = QScrollArea()
        self._issues_scroll.setWidgetResizable(True)
        self._issues_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._issues_scroll.setStyleSheet("QScrollArea { border: none; }")
        self._issues_scroll.setVisible(False)
        self._issues_body = QWidget()
        self._issues_body_layout = QVBoxLayout(self._issues_body)
        self._issues_body_layout.setContentsMargins(0, 2, 0, 0)
        self._issues_body_layout.setSpacing(2)
        self._issues_scroll.setWidget(self._issues_body)
        iss_layout.addWidget(self._issues_scroll)

        self._issues: list[tuple[str, str, str]] = []   # (reason, path, detail)
        self._issue_colors: dict[str, str] = {}
        self._issues_open = False
        root.addWidget(self._issues_frame)

        # Below the issue list, and quieter than it. This is the "what to do
        # about it" prose, and it is written per failure *class*, not per file
        # — bold, above the paths, it read as the answer while the three
        # specific paths underneath read as a footnote. It is the other way
        # round. It is also a sibling of the progress frame rather than a
        # child: a result is not progress, and parented inside it inherited
        # that frame's visibility and never appeared at all.
        self._result_lbl = QLabel("")
        self._result_lbl.setObjectName("Dim")
        self._result_lbl.setStyleSheet("font-size: 11px; margin-top: 10px;")
        self._result_lbl.setWordWrap(True)
        self._result_lbl.setVisible(False)
        root.addWidget(self._result_lbl)

        # ── Button row ────────────────────────────────────────────
        root.addSpacing(10)
        btn_row = QHBoxLayout()
        # Without this the two buttons sit against each other: the default
        # layout spacing is a couple of pixels at some styles and DPI
        # settings, and "Cancel" and "Move to Recycle Bin" read as one
        # control on the last screen before a deletion.
        btn_row.setSpacing(10)
        btn_row.addStretch()

        # Minimum, not fixed: these widths were measured against the English
        # labels, and a fixed width clips every language whose word is longer
        # (Ukrainian "Скасувати" needs 94px in the 90px this used to pin).
        self._btn_cancel = QPushButton(tr("Cancel"))
        self._btn_cancel.setObjectName("Subtle")
        self._btn_cancel.setMinimumWidth(90)
        self._btn_cancel.clicked.connect(self._on_cancel)
        btn_row.addWidget(self._btn_cancel)

        self._btn_confirm = QPushButton(tr("Move to Recycle Bin"))
        self._btn_confirm.setObjectName("Primary")
        self._btn_confirm.setMinimumWidth(180)
        self._btn_confirm.clicked.connect(self._on_confirm)
        btn_row.addWidget(self._btn_confirm)

        root.addLayout(btn_row)

        # Set initial sub-label + confirm button state
        self._update_sub_label()
        self._update_confirm_btn()

        # When the user has turned confirmation off, skip straight to the move
        # (still showing progress/result in this dialog) instead of asking.
        if self._auto_confirm and self._armed_targets():
            # Dress the dialog as progress *before* it is shown. It used to open
            # wearing its full confirmation face — title "Confirm Cleanup", a
            # "Don't ask again" tick, an armed "Move to Recycle Bin" button —
            # and then start deleting a frame later, so the user watched a
            # question they were never given the chance to answer.
            self._present_as_progress()
            QTimer.singleShot(0, self, self._on_confirm)

    def _present_as_progress(self):
        """Re-label the dialog as the operation it is about to perform.

        Only the asking parts go: the risk breakdown and the item counts stay,
        because "what is being removed right now" is exactly what a user wants
        to read while it happens — and Cancel stays live, since the worker
        checks for it between items.
        """
        self.setWindowTitle(tr("Moving to Recycle Bin"))
        self._header_lbl.setText(tr("Moving to Recycle Bin"))
        self._btn_confirm.setVisible(False)
        if self._dont_ask_cb:
            self._dont_ask_cb.setVisible(False)
        self._progress_frame.setVisible(True)

    def _present_as_result(self, headline: str, subline: str):
        """Swap the dialog from "what will happen" to "what happened".

        One state at a time. The risk breakdown, the review list, the cloud
        acknowledgment and the "will be sent" line all describe a plan; the
        moment the worker finishes they describe a plan that is no longer
        pending, and leaving them on screen above the outcome asked the user
        to work out which half was current.
        """
        self.setWindowTitle(headline)
        self._header_lbl.setText(headline)
        self._sub_lbl.setText(subline)
        for w in self._confirm_only:
            w.setVisible(False)
        for spacer in self._confirm_spacers:
            spacer.changeSize(0, 0, QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.layout().invalidate()
        if self._dont_ask_cb:
            self._dont_ask_cb.setVisible(False)
        if self._cloud_cb:
            self._cloud_cb.setVisible(False)
        # Deliberately no adjustSize() here. The dialog is only half rebuilt at
        # this point — the result text and the issue list are still to come —
        # and resizing to the half-built state pinned a geometry the eight-line
        # explanation then had to fit inside, so it was squeezed out of the
        # dialog entirely. _on_finished resizes once, at the end.

    # ── Issue list ────────────────────────────────────────────────

    def _populate_issues(self, result):
        """Turn the failure counts into named paths with a reason each."""
        errors = getattr(result, "errors_by_path", {}) or {}
        self._issues = []
        self._issue_colors = {}
        # Ordered worst-first: an irreversible deletion is the one line here a
        # user must not scroll past, and a protected skip — which is Podbye
        # working correctly — is the one they can ignore.
        for path in getattr(result, "not_recycled", []) or []:
            reason = tr("Deleted permanently")
            self._issue_colors[reason] = _risk_fg("Protected")
            self._issues.append((
                reason, path,
                tr("Too large for the Recycle Bin — this cannot be restored")))
        # Right after the irreversible line, because it is the same hazard
        # answered correctly: these are the items Podbye refused to destroy.
        for path, why in (getattr(result, "skipped_not_recyclable", {}) or {}).items():
            reason = tr("Kept on disk")
            self._issue_colors[reason] = _risk_fg("Review")
            self._issues.append((
                reason, path,
                tr("The Recycle Bin is turned off for this drive, so removing "
                   "it would have been permanent")
                if why == "bin_disabled" else
                tr("Too large for the Recycle Bin, so removing it would have "
                   "been permanent")))
        for path in result.failed:
            reason = tr("Failed")
            self._issue_colors[reason] = _risk_fg("Protected")
            self._issues.append((
                reason, path, errors.get(path) or tr("Unexpected error")))
        for path in result.in_use:
            reason = tr("In use")
            self._issue_colors[reason] = _risk_fg("Review")
            # Sentence first, code second. A locked file is an *expected*
            # outcome with a known cause, and "SHFileOperationW error 0x0020"
            # -- which is what the raw error actually says -- tells the person
            # reading it nothing they can act on. The code is kept after it so
            # Copy list still carries something diagnosable.
            detail = tr("A running program is holding this file open")
            raw = errors.get(path)
            if raw:
                detail = f"{detail}  ({raw})"
            self._issues.append((reason, path, detail))
        for path in result.skipped_protected:
            reason = tr("Protected")
            self._issue_colors[reason] = _risk_fg("Optional")
            self._issues.append((
                reason, path,
                tr("System-critical path — never deleted")))
        # Listed apart from Protected: this one is the user's own instruction,
        # and it can be taken back from Settings, which the other cannot.
        for path in getattr(result, "skipped_kept", []):
            reason = tr("Keep")
            self._issue_colors[reason] = _risk_fg("Optional")
            self._issues.append((
                reason, path,
                tr("You are keeping this — remove the mark in Settings to "
                   "clean it")))

        while self._issues_body_layout.count():
            item = self._issues_body_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.deleteLater()

        if not self._issues:
            self._issues_frame.setVisible(False)
            return

        pal = get_palette()
        dim = pal.get("text_dim", "#8a9b8f")
        # One column for the reason so the paths line up under each other. Run
        # together in a single label they started at three different offsets,
        # which is the one thing a monospaced list is supposed to prevent.
        reason_w = max(
            (QLabel(r).fontMetrics().horizontalAdvance(r)
             for r, _p, _d in self._issues), default=0) + 12
        for reason, path, detail in self._issues[:200]:
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(8)
            reason_lbl = QLabel(reason)
            reason_lbl.setFixedWidth(reason_w)
            reason_lbl.setStyleSheet(
                f"font-family: 'JetBrains Mono'; font-size: 10px; "
                f"color: {self._issue_colors.get(reason, dim)};")
            rl.addWidget(reason_lbl)
            lbl = ElidedLabel(path, mode=Qt.ElideMiddle)
            lbl.setStyleSheet(
                f"font-family: 'JetBrains Mono'; font-size: 10px; color: {dim};")
            lbl.setToolTip(f"{path}\n{detail}")
            rl.addWidget(lbl, stretch=1)
            self._issues_body_layout.addWidget(row)
        if len(self._issues) > 200:
            more = QLabel(tr("  … and {n} more", n=len(self._issues) - 200))
            more.setObjectName("Dim")
            more.setStyleSheet("font-size: 10px;")
            self._issues_body_layout.addWidget(more)
        self._issues_body_layout.addStretch()

        self._issues_frame.setVisible(True)
        self._update_issues_button()

    def _update_issues_button(self):
        self._btn_issues.setText(
            ("▼  " if self._issues_open else "▶  ")
            + tr_count("{n} item(s) need attention", len(self._issues), n=len(self._issues)))

    def _toggle_issues(self):
        self._issues_open = not self._issues_open
        self._issues_scroll.setVisible(self._issues_open)
        if self._issues_open:
            self._issues_scroll.setFixedHeight(
                min(160, self._issues_body.sizeHint().height() + 4))
        self._update_issues_button()
        self.adjustSize()

    def _copy_issue_list(self):
        """Put the paths on the clipboard — the fix usually happens elsewhere."""
        from PySide6.QtWidgets import QApplication
        text = "\n".join(f"{reason}\t{path}\t{detail}"
                         for reason, path, detail in self._issues)
        clip = QApplication.clipboard()
        if clip is not None:
            clip.setText(text)
        # Restored, so a second copy gets the same confirmation as the first —
        # a button stuck on "Copied" says nothing about the click just made.
        self._btn_copy_issues.setText(tr("Copied"))
        QTimer.singleShot(
            1500, self,
            lambda: self._btn_copy_issues.setText(tr("Copy list")))

    # ── Event handlers ────────────────────────────────────────────

    def _update_sub_label(self):
        """Describe what the confirm button will actually remove right now."""
        from app.models.deletion_scope import union_scope_bytes

        armed = self._armed_targets()
        # Not a plain sum of size_bytes. That is each entity's *exclusive*
        # share — what it contributes to a category total — while recycling a
        # folder takes the nested rows subtracted from it as well. This screen
        # is the last thing shown before anything is destroyed, so it states
        # the scope, and counts a nested selection once rather than twice.
        size = union_scope_bytes(armed)
        if not armed:
            self._sub_lbl.setText(tr("Nothing to remove."))
            return
        text = tr_count("{n} item(s) · {size} will be sent to the Recycle Bin",
                        len(armed), n=len(armed), size=_format_size(size))
        if self._review_targets:
            text += tr_count("  ·  includes {n} review/uncertain item(s)",
                             len(self._review_targets), n=len(self._review_targets))
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
        # Folders that hold a separately listed finding are expanded inside
        # the worker, off the UI thread — see _cleanup_targets_for_item.
        excludes = {f["path"]: list(f.get("cleanup_exclude_paths") or [])
                    for f in self._armed if f.get("cleanup_exclude_paths")}

        self._worker = CleanupWorker(
            paths=paths,
            mode=CleanupWorker.MODE_RECYCLE,
            exclude_by_path=excludes,
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
        n_skip = len(result.skipped_protected) + len(
            getattr(result, "skipped_kept", []))
        assessment = assess_cleanup_counts(
            succeeded_count=n_ok,
            in_use_count=n_in_use,
            failed_count=n_fail,
            skipped_count=n_skip,
            category_label="Selected items",
            retry_label="the cleanup",
            all_recoverable=not getattr(result, "not_recycled", None),
        )

        # Items the bin refused to absorb were removed for good. Reporting them
        # inside the "moved to Recycle Bin" count would print a recoverability
        # promise over a permanent deletion, so they are stated separately and
        # counted out of the recycled total.
        gone = list(getattr(result, "not_recycled", []))
        n_gone = len(gone)
        n_recycled = max(n_ok - n_gone, 0)

        # Build result text
        freed = _format_size(result.total_bytes_freed)
        if n_recycled:
            ok_msg = tr("✓  {count} item(s) moved to Recycle Bin · {freed} freed",
                        count=n_recycled, freed=freed)
        elif n_ok:
            ok_msg = tr("✓  {count} item(s) removed · {freed} freed",
                        count=n_ok, freed=freed)
        else:
            ok_msg = tr("No items were moved")

        # The outcome is stated once, in the dialog's own header and subtitle,
        # instead of being appended under a plan that is no longer pending.
        # The individual paths behind each count live in the issue list below,
        # so they are not summarised as numbers a second time here.
        self._present_as_result(
            tr("Cleanup complete") if not (n_in_use or n_fail)
            else tr("Cleanup finished with issues"),
            ok_msg,
        )
        self._populate_issues(result)

        parts = []
        if n_gone:
            parts.append(tr(
                "!  {n} item(s) were too large for the Recycle Bin and were "
                "removed permanently — these cannot be restored",
                n=n_gone,
            ))
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

        # Switch buttons: hide Cancel, change Confirm to Close.
        # setVisible(True) is load-bearing — _present_as_progress() hid this
        # button on the way in, and re-enabling and relabelling it is not the
        # same as showing it. Without this the finished dialog had no button at
        # all: the only way out was the titlebar ✕, on the one dialog that
        # reports what was just deleted.
        self._btn_cancel.setVisible(False)
        self._btn_confirm.setVisible(True)
        self._btn_confirm.setEnabled(True)
        self._btn_confirm.setText(tr("Close"))
        self._btn_confirm.clicked.disconnect()
        self._btn_confirm.clicked.connect(self.accept)

        # Resize once, now that the dialog is finally in its finished state:
        # the plan is hidden, the result text is set and the issue list exists.
        self.adjustSize()

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
