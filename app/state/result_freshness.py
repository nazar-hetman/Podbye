"""Whether results are fit to delete from.

Findings can be reopened from History long after the scan that produced them.
Two things can make that unsafe, and the cleanup entry point asks here first:

* **Outdated** - the rows were classified by an older classifier. They carry
  the risk and actionability they were saved with, so a rule that has since
  made Podbye more careful (Safe now needs evidence beyond a name) does not
  apply to them. Cleanup is refused until the target is scanned again.
* **Stale** - the scan is more than a day old. Folders are recycled whole,
  and their contents may have changed since anything looked at them. The user
  is asked before anything moves.
"""
from __future__ import annotations

USABLE = ""
STALE = "stale"
OUTDATED = "outdated"

STALE_AFTER_SECONDS = 24 * 60 * 60


def cleanup_gate(scan_state, now: float | None = None) -> str:
    """USABLE, STALE or OUTDATED for the results *scan_state* holds."""
    is_current = getattr(scan_state, "classifier_is_current", None)
    if callable(is_current) and not is_current():
        return OUTDATED
    age = getattr(scan_state, "results_age_seconds", None)
    if callable(age) and age(now) > STALE_AFTER_SECONDS:
        return STALE
    return USABLE
