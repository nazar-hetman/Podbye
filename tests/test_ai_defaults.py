"""Bulk AI is opt-in; per-item "Ask AI" always works.

A scan can produce hundreds of entities, and explaining every one at once
chokes a local model. So automatic explanation is off by default — but the
on-demand button must still work, or the feature is simply gone.
"""
from app.config.settings_store import _DEFAULTS


def test_bulk_findings_ai_is_off_by_default():
    assert _DEFAULTS["ai_findings_enabled"] is False


def test_ask_ai_bypasses_the_bulk_toggle():
    """explain_item must run even with bulk explanations disabled."""
    import inspect
    from app.services.ai_explainer import AIExplainer
    src = inspect.getsource(AIExplainer.explain_item)
    assert "force=True" in src, (
        "on-demand Ask AI no longer bypasses ai_findings_enabled — with bulk "
        "AI off by default that would leave no way to ask about an item")


def test_start_honours_force_over_the_toggle():
    import inspect
    from app.services.ai_explainer import AIExplainer
    src = inspect.getsource(AIExplainer.start)
    assert "force" in src and "ai_findings_enabled" in src


def test_startups_ai_is_unchanged():
    """Startups is a short, bounded list — no reason to make it opt-in."""
    assert _DEFAULTS["ai_startups_enabled"] is True
