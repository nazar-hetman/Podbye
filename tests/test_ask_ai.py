"""On-demand 'Ask AI' — a single item can be explained even when the bulk AI
pass is disabled, and the request is refused (with a reason) when no model is
configured."""
from app.services.ai_explainer import AIExplainer
from app.models.finding import Finding

# QApplication + offscreen platform come from the session fixture in conftest.py


class _FakeStore:
    def __init__(self, data):
        self._data = data

    def get(self, key, default=None):
        return self._data.get(key, default)


def _finding():
    return Finding(path=r"C:\x\q.bin", name="q.bin", is_dir=False, size_bytes=5,
                   extension=".bin", modified=0, accessed=0, parent=r"C:\x")


def test_explain_item_refuses_without_model():
    ai = AIExplainer(_FakeStore({"ai_model": "", "ai_findings_enabled": True}))
    f = _finding()
    assert ai.explain_item(f) == "no-model"
    # Item is left untouched so the UI keeps offering the button.
    assert f.ai_status == "none"


def test_explain_item_bypasses_disabled_bulk_toggle():
    # Bulk explanations are OFF, but an explicit single-item request must work.
    ai = AIExplainer(_FakeStore({"ai_model": "llama3", "ai_findings_enabled": False}))
    ai._running = True  # pretend a queue is active so no network dispatcher spawns
    f = _finding()
    reason = ai.explain_item(f)
    assert reason == ""
    assert f.ai_status == "pending"
    assert f in ai._queue
