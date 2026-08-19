"""The post-cleanup explanation has to read as prose, not as assembled parts.

It is stitched from a per-category rule plus generic lines added around it, and
nothing checked that the pieces did not say the same thing. The locked-file
fallback did exactly that: its rule context was "Windows file locks are normal
and do not mean cleanup failed." and the generic line under it was "This is
normal and does not mean cleanup failed."
"""
import re

import pytest

from app.services import cleanup_result_classifier as c
from app.services.cleanup_result_classifier import assess_cleanup_counts

CATEGORY_KEYS = [""] + sorted(c._EXPECTED_RULES)

# (succeeded, in_use, failed, skipped) — every branch the assembler has.
COUNT_CASES = [
    (10, 0, 0, 0),
    (0, 0, 0, 0),
    (0, 0, 0, 4),
    (10, 3, 0, 0),
    (0, 3, 0, 0),
    (10, 0, 2, 0),
    (10, 3, 2, 4),
]


def _sentences(text: str) -> list[str]:
    """Sentences, with bullets and headings stripped of their punctuation."""
    out = []
    for line in text.splitlines():
        line = line.strip().lstrip("•").strip()
        if not line:
            continue
        for part in re.split(r"(?<=[.!?])\s+", line):
            cleaned = part.strip().rstrip(".").strip().lower()
            if len(cleaned) > 12:
                out.append(cleaned)
    return out


@pytest.mark.parametrize("key", CATEGORY_KEYS)
@pytest.mark.parametrize("counts", COUNT_CASES)
def test_no_sentence_is_stated_twice(key, counts):
    ok, in_use, failed, skipped = counts
    text = assess_cleanup_counts(
        succeeded_count=ok, in_use_count=in_use, failed_count=failed,
        skipped_count=skipped, category_key=key, category_label="This category",
        retry_label="the cleanup",
    ).explanation_text
    seen, repeated = set(), []
    for sentence in _sentences(text):
        if sentence in seen:
            repeated.append(sentence)
        seen.add(sentence)
    assert not repeated, f"{key or 'fallback'}: repeated — {repeated}"


@pytest.mark.parametrize("key", CATEGORY_KEYS)
def test_a_rule_context_explains_rather_than_reassures(key):
    """The reassurance is added once, generically, around the rule. A rule
    whose own context reassures collides with it."""
    rule = c._EXPECTED_RULES.get(key) or c._fallback_expected_rule()
    context = rule["context"].lower()
    assert "does not mean cleanup failed" not in context
    assert "do not mean cleanup failed" not in context


@pytest.mark.parametrize("counts", COUNT_CASES)
def test_the_explanation_is_never_empty_or_ragged(counts):
    ok, in_use, failed, skipped = counts
    text = assess_cleanup_counts(
        succeeded_count=ok, in_use_count=in_use, failed_count=failed,
        skipped_count=skipped, category_label="This category",
        retry_label="the cleanup",
    ).explanation_text
    assert text.strip(), "no explanation at all"
    assert text == text.strip(), "leading or trailing blank lines"
    assert "\n\n\n" not in text, "a gap wide enough to read as a missing block"
