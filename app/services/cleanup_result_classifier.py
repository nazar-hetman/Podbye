"""Rule-based cleanup result classification and human-readable explanations.

Every user-facing string here goes through tr() **at call time**, not at import
time: the language can change while the app is running, and a table translated
once at import would keep whatever language was active at startup.

The rule tables below therefore hold English source text, which is also the
translation key.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.i18n import tr

STATE_SUCCESS = "success"
STATE_PARTIAL = "partial"
STATE_IN_USE = "in_use"
STATE_ALREADY_CLEAN = "already_clean"
STATE_SKIPPED = "skipped"
STATE_NOT_RECYCLABLE = "not_recyclable"
STATE_FAILED = "failed"

# Why the bin would not take an item, as recorded by cleanup_engine. Kept as
# codes rather than sentences so the reason survives a session round-trip and
# is translated here, once, at call time.
_NOT_RECYCLABLE_HEADLINE = {
    "too_large": "Too large for the Recycle Bin. Nothing was deleted.",
    "bin_disabled": ("The Recycle Bin is turned off for this drive. "
                     "Nothing was deleted."),
}
_NOT_RECYCLABLE_DETAIL = {
    "too_large": ("Windows answers a request the bin cannot hold by deleting "
                  "the item permanently and reporting success, so it was left "
                  "on disk instead. Empty the Recycle Bin or raise its size "
                  "limit for this drive, then try again."),
    "bin_disabled": ("This drive is set to remove files immediately rather "
                     "than to the Recycle Bin, so there would have been no way "
                     "back. Turn the Recycle Bin on for this drive, then try "
                     "again."),
}
_NOT_RECYCLABLE_BREAKDOWN = {
    "too_large": "too large for the bin",
    "bin_disabled": "Recycle Bin off",
}


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


# The `category_label` / `retry_label` values call sites pass in. They reach
# tr() through a variable, so no static scan over tr() literals finds them —
# listed here so the translation-coverage test can.
CALLER_LABELS = (
    "This category", "Cleanup run", "Selected items",
    "Quick Cleanup", "the cleanup",
)


def _fallback_expected_rule() -> dict:
    # Every rule's "context" explains the *mechanism*; the reassurance that
    # follows it is added once, generically, by assess_cleanup_counts. This one
    # used to be the reassurance itself, so a locked-file result printed
    # "Windows file locks are normal and do not mean cleanup failed." directly
    # above "This is normal and does not mean cleanup failed."
    return {
        "intro": "Some files are still being used by Windows or another app.",
        "context": ("A file that is open cannot be moved, so Podbye left those "
                    "where they are rather than forcing them."),
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
    not_recyclable_count: int = 0,
    not_recyclable_reason: str = "",
    category_key: str | None = None,
    category_label: str | None = None,
    retry_label: str = "Quick Cleanup",
    all_recoverable: bool = True,
) -> CleanupAssessment:
    """Convert cleanup counters into calm, user-facing result language.

    ``retry_label`` names the action the user should repeat to finish the
    cleanup (e.g. "Quick Cleanup" or "the cleanup"). It keeps the guidance
    accurate regardless of which flow triggered the cleanup.

    Both labels are English keys and are translated here, not by the caller —
    every call site passed a raw literal, so a Ukrainian user was told to
    "запустити «the cleanup» ще раз".
    """
    label = tr(category_label) if category_label else tr("This category")
    retry_label = tr(retry_label)
    # Items the bin refused are a skip like any other for counting purposes.
    # Leaving them out was how a refusal reached the "nothing removable left"
    # branch below: every counter read zero, so a 15 GB folder Podbye had
    # deliberately protected was reported as an empty category.
    total_skipped = skipped_count + not_recyclable_count

    nothing_happened = (succeeded_count == 0 and in_use_count == 0
                        and failed_count == 0)

    # Said before the generic skip branch, because "nothing was deleted" and
    # *why* is the whole message here: the item is still on disk, it is not
    # protected, and the user can act on the reason.
    if nothing_happened and skipped_count == 0 and not_recyclable_count > 0:
        headline = _NOT_RECYCLABLE_HEADLINE.get(
            not_recyclable_reason,
            "Nothing was deleted — the Recycle Bin would not take it.")
        detail = _NOT_RECYCLABLE_DETAIL.get(
            not_recyclable_reason,
            "The item was left on disk rather than removed permanently.")
        return CleanupAssessment(
            state=STATE_NOT_RECYCLABLE,
            succeeded_count=succeeded_count,
            in_use_count=in_use_count,
            failed_count=failed_count,
            skipped_count=total_skipped,
            short_label=tr("nothing deleted"),
            item_label=tr("{n:,} item(s) kept on disk", n=not_recyclable_count),
            breakdown_label=tr(_NOT_RECYCLABLE_BREAKDOWN.get(
                not_recyclable_reason, "not recyclable")),
            summary_key_label=tr("Kept on disk"),
            summary_value=tr("{n:,} not recyclable", n=not_recyclable_count),
            explanation_text=tr(headline) + "\n\n" + tr(detail),
        )

    if nothing_happened and total_skipped == 0:
        return CleanupAssessment(
            state=STATE_ALREADY_CLEAN,
            succeeded_count=succeeded_count,
            in_use_count=in_use_count,
            failed_count=failed_count,
            skipped_count=skipped_count,
            short_label=tr("already clean"),
            item_label=tr("nothing removable found"),
            breakdown_label=tr("already clean"),
            summary_key_label=tr("Status"),
            summary_value=tr("Already clean"),
            explanation_text=(
                tr("There was nothing removable left in {label}.", label=label)
                + "\n\n"
                + tr("This usually means the category was already cleaned or "
                     "there was nothing left for {retry} to remove.",
                     retry=retry_label)
            ),
        )

    # Only protected items were skipped — nothing was actually moved. Without
    # this branch the code fell through to STATE_SUCCESS and told the user the
    # category was "cleaned successfully · all files moved safely", directly
    # contradicting the "No items were moved · N protected skipped" line above it.
    if nothing_happened and total_skipped > 0:
        return CleanupAssessment(
            state=STATE_SKIPPED,
            succeeded_count=succeeded_count,
            in_use_count=in_use_count,
            failed_count=failed_count,
            skipped_count=total_skipped,
            short_label=tr("nothing to clean"),
            item_label=tr("{n:,} protected item(s) skipped", n=skipped_count),
            breakdown_label=tr("all protected"),
            summary_key_label=tr("Skipped"),
            summary_value=tr("{n:,} protected", n=skipped_count),
            explanation_text=(
                tr("Nothing was removed from {label}.", label=label) + "\n\n"
                + tr("{n:,} item(s) are protected and were skipped to keep your "
                     "system safe — protected items are never moved to the "
                     "Recycle Bin.", n=skipped_count)
            ),
        )

    if failed_count > 0:
        value = tr("{n:,} unexpected issue(s)", n=failed_count)
        if in_use_count:
            value = tr("{locked:,} in use · {failed:,} need attention",
                       locked=in_use_count, failed=failed_count)
        # A run that moved nothing failed. A run that moved most of a category
        # and hit an error on the rest did not — it is partial, the same as one
        # blocked by locked files, and calling it "Attention" in red put a
        # cleanup that freed 40 GB in the same bucket as one that freed none.
        #
        # The difference from the in-use case is the *reason*, not the outcome,
        # and every word below still says so: the summary counts the unexpected
        # issues and the explanation is about them. Only the verdict changes.
        state = STATE_PARTIAL if succeeded_count > 0 else STATE_FAILED
        return CleanupAssessment(
            state=state,
            succeeded_count=succeeded_count,
            in_use_count=in_use_count,
            failed_count=failed_count,
            skipped_count=skipped_count,
            short_label=(tr("partly cleaned") if succeeded_count
                         else tr("needs attention")),
            item_label=(
                tr("✓  {moved:,} moved · {failed:,} unexpected",
                   moved=succeeded_count, failed=failed_count)
                if succeeded_count
                else tr("{n:,} could not be cleaned", n=failed_count)
            ),
            breakdown_label=(tr("partly cleaned") if succeeded_count
                             else tr("needs attention")),
            summary_key_label=tr("Failed") if not in_use_count else tr("Status"),
            summary_value=value,
            explanation_text=(
                tr("Windows returned an unexpected cleanup error for part of "
                   "this category.") + "\n\n"
                + tr("This is different from a normal locked-file case, so Podbye "
                     "left those items alone instead of forcing the cleanup.")
                + "\n\n" + tr("You can:") + "\n"
                + "• " + tr("restart Windows") + "\n"
                + "• " + tr("run {retry} again", retry=retry_label) + "\n"
                + "• " + tr("if this keeps happening, review the affected folder "
                            "manually")
            ),
        )

    if in_use_count > 0:
        rule = _EXPECTED_RULES.get(category_key or "", _fallback_expected_rule())
        intro = tr(rule["intro"])
        if succeeded_count > 0:
            intro = tr("Most removable files were cleaned.") + " " + intro
            state = STATE_PARTIAL
            short_label = tr("partial cleanup")
            item_label = tr("✓  {moved:,} moved · {locked:,} locked",
                            moved=succeeded_count, locked=in_use_count)
            breakdown_label = tr("partial cleanup")
        else:
            state = STATE_IN_USE
            short_label = tr("in use by system")
            item_label = tr("{n:,} files currently in use", n=in_use_count)
            breakdown_label = tr("in use by system")

        # Each action is a translation key in its own right; {retry} is filled
        # after translation so the placeholder survives.
        actions = [tr(action, retry=retry_label) for action in rule["actions"]]
        explanation = (
            f"{intro}\n\n"
            + tr(rule["context"]) + "\n\n"
            + tr("This is normal and does not mean cleanup failed.") + "\n\n"
            + tr("To fully clean this category:") + "\n"
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
            summary_key_label=tr("Skipped"),
            summary_value=tr("{n:,} locked / in use", n=in_use_count),
            explanation_text=explanation,
            actions=actions,
        )

    return CleanupAssessment(
        state=STATE_SUCCESS,
        succeeded_count=succeeded_count,
        in_use_count=in_use_count,
        failed_count=failed_count,
        skipped_count=skipped_count,
        short_label=tr("cleaned"),
        item_label=tr("✓  {n:,} moved", n=succeeded_count),
        breakdown_label=tr("cleaned"),
        summary_key_label=tr("Status"),
        summary_value=tr("Cleaned successfully"),
        explanation_text=(
            tr("{label} was cleaned successfully.", label=label) + "\n\n"
            + (tr("All removable files in this category were moved safely. "
                  "Anything that was cleaned remains recoverable in the "
                  "Recycle Bin.")
               if all_recoverable else
               # Something in this batch never reached the bin, so the blanket
               # promise would contradict the warning printed beside it.
               tr("All removable files in this category were removed. Items "
                  "the Recycle Bin could not hold were deleted permanently "
                  "and are listed above; the rest can still be restored."))
        ),
    )
