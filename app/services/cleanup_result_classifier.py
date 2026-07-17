"""Rule-based cleanup result classification and human-readable explanations."""
from __future__ import annotations

from dataclasses import dataclass, field


STATE_SUCCESS = "success"
STATE_PARTIAL = "partial"
STATE_IN_USE = "in_use"
STATE_ALREADY_CLEAN = "already_clean"
STATE_FAILED = "failed"


@dataclass
class CleanupAssessment:
    state: str
    succeeded_count: int
    in_use_count: int
    failed_count: int
    skipped_count: int
    short_label: str
    item_label: str
    breakdown_label: str
    summary_key_label: str
    summary_value: str
    explanation_text: str
    actions: list[str] = field(default_factory=list)


_EXPECTED_RULES = {
    "thumbnail_cache": {
        "intro": "Some thumbnail files are still being used by Windows Explorer.",
        "context": (
            "Windows creates preview images automatically when you browse folders "
            "with pictures or videos. Some of these preview files may stay active "
            "while Explorer is open."
        ),
        "actions": [
            "close open File Explorer windows",
            "or restart Windows",
            "then run {retry} again",
        ],
    },
    "browser_cache": {
        "intro": "Some browser cache files are still in use because the browser is running in the background.",
        "context": (
            "Browsers often keep cache files open even after the main window is closed."
        ),
        "actions": [
            "close the browser completely",
            "make sure no background browser processes remain",
            "then run {retry} again",
        ],
    },
    "windows_update": {
        "intro": "Some update files are currently being used by Windows Update services.",
        "context": (
            "This often happens shortly after Windows updates or while background "
            "update tasks are still active."
        ),
        "actions": [
            "retry later",
            "or restart Windows",
            "then run {retry} again",
        ],
    },
    "windows_temp": {
        "intro": "Some temporary system files are still being used by Windows.",
        "context": (
            "Background services and installers can keep temp files open while work "
            "is still in progress."
        ),
        "actions": [
            "let Windows finish background tasks",
            "or restart Windows",
            "then run {retry} again",
        ],
    },
    "user_temp": {
        "intro": "Some temporary files are still being used by Windows or another app.",
        "context": (
            "This is common when apps or installers are still open in the background."
        ),
        "actions": [
            "close the app that may still be using the files",
            "or restart Windows",
            "then run {retry} again",
        ],
    },
}


def _fallback_expected_rule() -> dict:
    return {
        "intro": "Some files are still being used by Windows or another app.",
        "context": "Windows file locks are normal and do not mean cleanup failed.",
        "actions": [
            "close the app using the files, if known",
            "or restart Windows",
            "then run {retry} again",
        ],
    }


def assess_cleanup_counts(
    *,
    succeeded_count: int,
    in_use_count: int,
    failed_count: int,
    skipped_count: int = 0,
    category_key: str | None = None,
    category_label: str | None = None,
    retry_label: str = "Quick Cleanup",
) -> CleanupAssessment:
    """Convert cleanup counters into calm, user-facing result language.

    ``retry_label`` names the action the user should repeat to finish the
    cleanup (e.g. "Quick Cleanup" or "the cleanup"). It keeps the guidance
    accurate regardless of which flow triggered the cleanup.
    """
    label = category_label or "This category"

    if succeeded_count == 0 and in_use_count == 0 and failed_count == 0 and skipped_count == 0:
        return CleanupAssessment(
            state=STATE_ALREADY_CLEAN,
            succeeded_count=succeeded_count,
            in_use_count=in_use_count,
            failed_count=failed_count,
            skipped_count=skipped_count,
            short_label="already clean",
            item_label="nothing removable found",
            breakdown_label="already clean",
            summary_key_label="Status",
            summary_value="Already clean",
            explanation_text=(
                f"There was nothing removable left in {label}.\n\n"
                "This usually means the category was already cleaned or there was "
                f"nothing left for {retry_label} to remove."
            ),
        )

    if failed_count > 0:
        value = f"{failed_count:,} unexpected issue(s)"
        if in_use_count:
            value = f"{in_use_count:,} in use · {failed_count:,} need attention"
        return CleanupAssessment(
            state=STATE_FAILED,
            succeeded_count=succeeded_count,
            in_use_count=in_use_count,
            failed_count=failed_count,
            skipped_count=skipped_count,
            short_label="needs attention",
            item_label=(
                f"✓  {succeeded_count:,} moved · {failed_count:,} unexpected"
                if succeeded_count
                else f"{failed_count:,} could not be cleaned"
            ),
            breakdown_label="needs attention",
            summary_key_label="Failed" if not in_use_count else "Status",
            summary_value=value,
            explanation_text=(
                "Windows returned an unexpected cleanup error for part of this category.\n\n"
                "This is different from a normal locked-file case, so Vigil left those "
                "items alone instead of forcing the cleanup.\n\n"
                "You can:\n"
                "• restart Windows\n"
                f"• run {retry_label} again\n"
                "• if this keeps happening, review the affected folder manually"
            ),
        )

    if in_use_count > 0:
        rule = _EXPECTED_RULES.get(category_key or "", _fallback_expected_rule())
        intro = rule["intro"]
        if succeeded_count > 0:
            intro = f"Most removable files were cleaned. {intro}"
            state = STATE_PARTIAL
            short_label = "partial cleanup"
            item_label = f"✓  {succeeded_count:,} moved · {in_use_count:,} locked"
            breakdown_label = "partial cleanup"
        else:
            state = STATE_IN_USE
            short_label = "in use by system"
            item_label = f"{in_use_count:,} files currently in use"
            breakdown_label = "in use by system"

        actions = [action.format(retry=retry_label) for action in rule["actions"]]
        explanation = (
            f"{intro}\n\n"
            f"{rule['context']}\n\n"
            "This is normal and does not mean cleanup failed.\n\n"
            "To fully clean this category:\n"
            + "\n".join(f"• {action}" for action in actions)
        )
        return CleanupAssessment(
            state=state,
            succeeded_count=succeeded_count,
            in_use_count=in_use_count,
            failed_count=failed_count,
            skipped_count=skipped_count,
            short_label=short_label,
            item_label=item_label,
            breakdown_label=breakdown_label,
            summary_key_label="Skipped",
            summary_value=f"{in_use_count:,} locked / in use",
            explanation_text=explanation,
            actions=actions,
        )

    return CleanupAssessment(
        state=STATE_SUCCESS,
        succeeded_count=succeeded_count,
        in_use_count=in_use_count,
        failed_count=failed_count,
        skipped_count=skipped_count,
        short_label="cleaned",
        item_label=f"✓  {succeeded_count:,} moved",
        breakdown_label="cleaned",
        summary_key_label="Status",
        summary_value="Cleaned successfully",
        explanation_text=(
            f"{label} was cleaned successfully.\n\n"
            "All removable files in this category were moved safely. "
            "Anything that was cleaned remains recoverable in the Recycle Bin."
        ),
    )
